"""The Hermes receiver plugin, tested without Hermes.

A fake ctx stands in for PluginContext: it records what inject_message was
given. The plugin's HTTP handler is then exercised over a real socket, so the
contract (origin targeting, token check, marker prefix) is checked end to end.
"""

import importlib.util
import json
import pathlib
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

PLUGIN = pathlib.Path(__file__).resolve().parents[1] / "plugins" / "watchlink-receiver" / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("watchlink_receiver", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    """The slice of PluginContext the plugin uses, and what it was told."""

    def __init__(self, accepted: bool = True):
        self.accepted = accepted
        self.injected: list = []

    def get_config(self, key, default=None):
        return {"platform": "telegram", "chat_id": "6574561645",
                "host": "127.0.0.1", "port": "0",
                "token_env": "WATCHLINK_RECEIVER_TOKEN"}.get(key, default)

    def inject_message(self, content, role="user", *, session_key=None, origin=None):
        self.injected.append({"content": content, "role": role,
                              "session_key": session_key, "origin": origin})
        return self.accepted


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin()
        self.ctx = FakeCtx()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         self.plugin.make_handler(self.ctx, self.plugin.settings(self.ctx)))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()

    def post(self, body: dict, token: str = "") -> tuple[int, dict]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/capture",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_a_capture_is_injected_into_the_configured_chat(self):
        code, out = self.post({"id": "rec-7", "text": "remind me to sleep in one hour"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["injected"], True)
        sent = self.ctx.injected[-1]
        self.assertEqual(sent["origin"], {"platform": "telegram", "chat_id": "6574561645"})
        self.assertEqual(sent["role"], "user")
        self.assertIn("rec-7", sent["content"])
        self.assertIn("remind me to sleep in one hour", sent["content"])

    def test_the_marker_says_it_may_be_other_speakers(self):
        self.post({"id": "rec-8", "text": "hi"})
        self.assertIn("may include other speakers", self.ctx.injected[-1]["content"])

    def test_the_token_is_required_when_configured(self):
        import os
        os.environ["WATCHLINK_RECEIVER_TOKEN"] = "s3cret"
        server = ThreadingHTTPServer(("127.0.0.1", 0),
                                     self.plugin.make_handler(self.ctx, self.plugin.settings(self.ctx)))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/capture", data=b'{"id":"x","text":"y"}',
                                         method="POST")
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(raised.exception.code, 401)
            self.assertEqual(self.ctx.injected, [])
        finally:
            server.shutdown()
            del os.environ["WATCHLINK_RECEIVER_TOKEN"]

    def test_a_refused_injection_answers_409(self):
        self.ctx.accepted = False
        code, out = self.post({"id": "rec-9", "text": "hi"})
        self.assertEqual(code, 409)
        self.assertEqual(out["injected"], False)


if __name__ == "__main__":
    unittest.main()
