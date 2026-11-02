import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from api import make_server


class APITests(unittest.TestCase):
    def test_http_review_lifecycle_and_stale_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            server = make_server(str(Path(temp)/"queue.db"), 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:%d" % server.server_port
            def post(route, data):
                request = urllib.request.Request(base+route, data=json.dumps(data).encode(),
                                                 headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=3) as response:
                    return json.load(response)
            try:
                self.assertTrue(post("/tasks", {"id": "demo", "payload": {"candidates": ["Demo A", "Demo B"]}})["created"])
                claim = post("/claims", {"reviewer": "demo-reviewer"})["claim"]
                decision = dict(id="demo", reviewer="demo-reviewer", token=claim["token"], decision="keep_separate")
                self.assertEqual(post("/decisions", decision)["status"], "completed")
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post("/decisions", decision)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
                with urllib.request.urlopen(base+"/tasks", timeout=3) as response:
                    self.assertEqual(json.load(response)["tasks"][0]["status"], "completed")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
