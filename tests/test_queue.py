import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path
from review_queue import Queue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "queue.db")
        self.queue = Queue(self.path)
        self.addCleanup(self.queue.close)

    def test_priority_then_fifo(self):
        self.queue.enqueue("low", {}, 10, now=1)
        self.queue.enqueue("first", {}, 90, now=2)
        self.queue.enqueue("second", {}, 90, now=3)
        self.assertEqual(self.queue.claim("a", now=4)["id"], "first")
        self.assertEqual(self.queue.claim("b", now=4)["id"], "second")

    def test_two_connections_cannot_claim_same_task(self):
        self.queue.enqueue("one", {})
        barrier = threading.Barrier(2)
        def work(name):
            q = Queue(self.path)
            try:
                barrier.wait(timeout=5)
                return q.claim(name)
            finally:
                q.close()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(work, ["a", "b"]))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_expired_claim_is_recovered_and_old_token_fenced(self):
        self.queue.enqueue("one", {})
        old = self.queue.claim("a", lease_seconds=10, now=0)
        new = self.queue.claim("a", lease_seconds=10, now=10)
        self.assertNotEqual(old["token"], new["token"])
        with self.assertRaises(ValueError):
            self.queue.complete("one", "a", old["token"], "merge", now=11)
        self.queue.complete("one", "a", new["token"], "merge", now=11)
        self.assertEqual(self.queue.snapshot()[0]["status"], "completed")

    def test_wrong_owner_and_expiry_rejected(self):
        self.queue.enqueue("one", {})
        claim = self.queue.claim("a", lease_seconds=10, now=0)
        for owner, now in [("b", 1), ("a", 10)]:
            with self.assertRaises(ValueError):
                self.queue.complete("one", owner, claim["token"], "merge", now=now)

    def test_enqueue_replay_and_payload_collision(self):
        self.assertTrue(self.queue.enqueue("one", {"a": 1}))
        self.assertFalse(self.queue.enqueue("one", {"a": 1}))
        with self.assertRaises(ValueError):
            self.queue.enqueue("one", {"a": 2})

    def test_completion_is_audited_and_not_reclaimable(self):
        self.queue.enqueue("one", {})
        claim = self.queue.claim("a", now=0)
        self.queue.complete("one", "a", claim["token"], "keep_separate", now=1)
        self.assertIsNone(self.queue.claim("b", now=100))
        actions = [r[0] for r in self.queue.db.execute("SELECT action FROM events ORDER BY id")]
        self.assertEqual(actions, ["enqueued", "claimed", "completed:keep_separate"])
