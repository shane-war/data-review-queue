"""A durable review queue with leased claims and fencing tokens."""
import argparse
import json
import sqlite3
import time
import uuid


class Queue:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, priority INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', owner TEXT, token TEXT,
                lease_until REAL, created REAL NOT NULL, decision TEXT);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, task_id TEXT, action TEXT, actor TEXT, at REAL);
            CREATE INDEX IF NOT EXISTS tasks_claim ON tasks(status, priority, created);
        """)

    def close(self):
        self.db.close()

    def enqueue(self, task_id, payload, priority=50, now=None):
        if not isinstance(task_id, str) or not task_id.strip() or type(priority) is not int or not 0 <= priority <= 100:
            raise ValueError("Task ID and priority between 0 and 100 required")
        encoded = json.dumps(payload, sort_keys=True)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT payload,priority FROM tasks WHERE id=?", (task_id,)).fetchone()
            if old:
                if (old["payload"], old["priority"]) != (encoded, priority):
                    raise ValueError("Task ID already represents different work")
                self.db.commit()
                return False
            timestamp = time.time() if now is None else now
            self.db.execute("INSERT INTO tasks(id,payload,priority,created) VALUES (?,?,?,?)",
                            (task_id, encoded, priority, timestamp))
            self.db.execute("INSERT INTO events(task_id,action,actor,at) VALUES (?,'enqueued','system',?)",
                            (task_id, timestamp))
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise

    def claim(self, reviewer, lease_seconds=60, now=None):
        if not isinstance(reviewer, str) or not reviewer.strip() or type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("Reviewer and lease of 1..3600 seconds required")
        timestamp = time.time() if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            task = self.db.execute("""
                SELECT * FROM tasks WHERE status='pending'
                   OR (status='claimed' AND lease_until<=?)
                ORDER BY priority DESC, created, id LIMIT 1
            """, (timestamp,)).fetchone()
            if task is None:
                self.db.commit()
                return None
            token = str(uuid.uuid4())
            self.db.execute("""UPDATE tasks SET status='claimed',owner=?,token=?,lease_until=?
                               WHERE id=?""", (reviewer, token, timestamp+lease_seconds, task["id"]))
            action = "reclaimed" if task["status"] == "claimed" else "claimed"
            self.db.execute("INSERT INTO events(task_id,action,actor,at) VALUES (?,?,?,?)",
                            (task["id"], action, reviewer, timestamp))
            self.db.commit()
            return dict(id=task["id"], payload=json.loads(task["payload"]), owner=reviewer,
                        token=token, lease_until=timestamp+lease_seconds)
        except BaseException:
            self.db.rollback()
            raise

    def complete(self, task_id, reviewer, token, decision, now=None):
        if decision not in ("merge", "keep_separate", "needs_information"):
            raise ValueError("Unsupported review decision")
        timestamp = time.time() if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.db.execute("""
                UPDATE tasks SET status='completed',decision=?
                WHERE id=? AND owner=? AND token=? AND status='claimed' AND lease_until>?
            """, (decision, task_id, reviewer, token, timestamp))
            if cursor.rowcount != 1:
                raise ValueError("Claim expired, replaced, completed, or owned by another reviewer")
            self.db.execute("INSERT INTO events(task_id,action,actor,at) VALUES (?,?,?,?)",
                            (task_id, "completed:"+decision, reviewer, timestamp))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def snapshot(self):
        return [dict(row) for row in self.db.execute(
            "SELECT id,priority,status,owner,decision FROM tasks ORDER BY id")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=":memory:")
    args = parser.parse_args()
    queue = Queue(args.db)
    try:
        queue.enqueue("REVIEW-001", {"candidates": ["Juniper Supply", "Juniper Workshop Supply"]}, 90)
        queue.enqueue("REVIEW-002", {"candidates": ["Cedar Parts", "Cedar Components"]}, 40)
        claim = queue.claim("reviewer-demo")
        if claim:
            queue.complete(claim["id"], claim["owner"], claim["token"], "keep_separate")
        print(json.dumps({"claim": claim, "tasks": queue.snapshot()}, indent=2))
    finally:
        queue.close()


if __name__ == "__main__":
    main()
