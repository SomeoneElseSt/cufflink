"""The pipeline, end to end, on a fake local server: deliver -> routing order.

Runs with the stdlib only (python -m unittest), no whisper, no network:
the receiver is a local HTTP server, the transcription provider is not needed.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from watchlink import destinations as D, routing as R


class Receiver(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Receiver.received.append(json.loads(body or b"{}"))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


class PipelineTest(unittest.TestCase):
    def test_a_capture_delivers_to_the_receiver(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            dest = D.Destination(name="receiver", url=f"http://127.0.0.1:{port}", scheme="none")
            result = D.deliver(dest, {"id": "rec-1", "ts": "2026-11-15T00:00:00+0000",
                                      "text": "hello", "message": "hello"})
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(Receiver.received[-1].get("message"), "hello")
        finally:
            server.shutdown()

    def test_routing_order_is_first_match_wins(self):
        routes = [
            {"name": "urgent", "match": {"any": ["remind"]}, "dest": "telegram"},
            {"name": "delegate", "match": {"any": ["sub-agent"]}, "dest": "hermes"},
            {"name": "fallback", "match": {"always": True}, "dest": "inbox"},
        ]
        self.assertEqual(R.naive(routes, "remind me to sleep"), ("telegram", "urgent"))
        self.assertEqual(R.naive(routes, "send a sub-agent"), ("hermes", "delegate"))
        self.assertEqual(R.naive(routes, "something else"), ("inbox", "fallback"))

    def test_no_routes_means_the_first_destination(self):
        class Cfg:
            routes = []
            destinations = {"inbox": object()}
            routing = {"type": "naive"}
        self.assertEqual(R.route(Cfg(), "anything"), ("inbox", "default"))


if __name__ == "__main__":
    unittest.main()
