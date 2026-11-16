"""The Hermes receiver plugin, tested without Hermes.

A fake ctx stands in for PluginContext: it keeps a state dict, records
injections, and can fire the post_gateway_admission hook like the gateway does.
The HTTP handler is exercised over a real socket, so the contract - learn the
route from a message, inject by session key, refuse when there is no route - is
checked end to end.
"""

import importlib.util
import json
import os
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


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeCtx:
    """The slice of PluginContext the plugin uses, and what it was told."""

    def __init__(self, accepted: bool = True):
        self.accepted = accepted
        self.injected: list = []
        self.state = FakeState()
        self.hooks = {}

    def get_config(self, key, default=None):
        return {"host": "127.0.0.1", "port": "0",
                "token_env": "WATCHLINK_RECEIVER_TOKEN", "dm_only": "true"}.get(key, default)

    def register_hook(self, name, callback, **kwargs):
        self.hooks[name] = callback

    def inject_message(self, content, role="user", *, session_key=None, origin=None):
        self.injected.append({"content": content, "role": role,
                              "session_key": session_key, "origin": origin})
        return self.accepted

    def fire_admission(self, session_key, platform="telegram", source=None, text="hi"):
        return self.hooks["post_gateway_admission"](session_key=session_key, platform=platform,
                                                    source=source or {"chat_type": "dm"}, text=text)


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin()
        self.ctx = FakeCtx()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         self.plugin.make_handler(self.ctx, self.plugin.settings(self.ctx)))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.plugin.remember_route(self.ctx, dm_only=True)

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

    def test_without_a_route_the_capture_is_refused_so_it_stays_queued(self):
        code, out = self.post({"id": "rec-1", "text": "hello"})
        self.assertEqual(code, 503)
        self.assertEqual(out["injected"], False)
        self.assertEqual(self.ctx.injected, [])

    def test_a_user_message_teaches_the_route_and_it_is_never_consumed(self):
        self.assertIsNone(self.ctx.fire_admission("agent:main:telegram:dm:6574561645"))
        self.assertEqual(self.ctx.state.get("route"),
                         {"session_key": "agent:main:telegram:dm:6574561645", "platform": "telegram"})

    def test_a_group_chat_is_not_learned_as_the_route(self):
        self.ctx.fire_admission("agent:main:telegram:group:-100", source={"chat_type": "group"})
        self.assertIsNone(self.ctx.state.get("route"))

    def test_a_capture_goes_to_the_learned_session(self):
        self.ctx.fire_admission("agent:main:telegram:dm:6574561645")
        code, out = self.post({"id": "rec-7", "text": "remind me to sleep in one hour"})
        self.assertEqual(code, 200, out)
        sent = self.ctx.injected[-1]
        self.assertEqual(sent["session_key"], "agent:main:telegram:dm:6574561645")
        self.assertEqual(sent["role"], "user")
        self.assertIn("rec-7", sent["content"])
        self.assertIn("remind me to sleep in one hour", sent["content"])

    def test_the_marker_says_it_may_be_other_speakers(self):
        self.ctx.fire_admission("agent:main:telegram:dm:6574561645")
        self.post({"id": "rec-8", "text": "hi"})
        self.assertIn("may include other speakers", self.ctx.injected[-1]["content"])

    def test_the_token_is_required_when_configured(self):
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
        self.ctx.fire_admission("agent:main:telegram:dm:6574561645")
        self.ctx.accepted = False
        code, out = self.post({"id": "rec-9", "text": "hi"})
        self.assertEqual(code, 409)
        self.assertEqual(out["injected"], False)


if __name__ == "__main__":
    unittest.main()
