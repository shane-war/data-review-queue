"""Loopback-only HTTP interface for the review queue; demo authentication is absent."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from review_queue import Queue


def make_server(path, port=8080):
    queue = Queue(path)
    queue.close()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/tasks":
                self.respond(404, {"error": "Route not found"})
                return
            q = Queue(path)
            try:
                self.respond(200, {"tasks": q.snapshot()})
            finally:
                q.close()

        def do_POST(self):
            q = Queue(path)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    self.respond(413, {"error": "Request must contain 1..16384 bytes"})
                    return
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                if self.path == "/tasks":
                    created = q.enqueue(body["id"], body["payload"], body.get("priority", 50))
                    self.respond(201 if created else 200, {"created": created})
                elif self.path == "/claims":
                    claim = q.claim(body["reviewer"], body.get("lease_seconds", 60))
                    self.respond(200, {"claim": claim})
                elif self.path == "/decisions":
                    q.complete(body["id"], body["reviewer"], body["token"], body["decision"])
                    self.respond(200, {"status": "completed"})
                else:
                    self.respond(404, {"error": "Route not found"})
            except (ValueError, KeyError, TypeError):
                self.respond(400, {"error": "Invalid request or invalid/expired claim"})
            finally:
                q.close()
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="review-demo.db")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = make_server(args.db, args.port)
    print("Demo API listening at http://127.0.0.1:%d" % server.server_port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
