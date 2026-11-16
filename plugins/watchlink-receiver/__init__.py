"""watchlink-receiver: a Hermes plugin that puts a wrist capture into YOUR chat.

The flow it completes:

    Watch -> watchlink transcribes -> watchlink route -> this plugin -> this chat

Why a plugin and not a webhook: Hermes's inject_message API has no public HTTP
endpoint - the plugin must already know which chat to target. So the plugin
targets the chat by ``origin`` (platform + chat_id), which resolves to the
existing session, and the gateway queues the text there as a user turn.

Safety: a transcript is not necessarily an instruction from you - the watch
microphone hears other people too. Each injected turn is prefixed with a
marker naming the capture id, so the agent can tell captured audio apart from
something you typed.

Config, under plugins.entries.watchlink-receiver.settings in config.yaml:

    platform: telegram
    chat_id: "6574561645"
    host: 127.0.0.1
    port: 8770
    token_env: WATCHLINK_RECEIVER_TOKEN   # bearer token the sender must present
"""

import json
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("watchlink.receiver")

DEFAULTS = {"platform": "telegram", "chat_id": "6574561645",
            "host": "127.0.0.1", "port": "8770",
            "token_env": "WATCHLINK_RECEIVER_TOKEN"}


def settings(ctx) -> dict:
    """The plugin's own settings, read through ctx.get_config (env as fallback)."""
    merged = dict(DEFAULTS)
    for key in DEFAULTS:
        value = ctx.get_config(key, None) if hasattr(ctx, "get_config") else None
        if value in (None, ""):
            value = os.environ.get(f"WATCHLINK_{key.upper()}", "")
        if value not in (None, ""):
            merged[key] = str(value)
    return merged


def injection_text(capture: dict) -> str:
    """What the agent sees: a marker saying where it came from, then the transcript."""
    rid = capture.get("id", "?")
    text = (capture.get("text") or capture.get("message") or "").strip()
    return (f"[watchlink capture {rid} - recorded from the wrist microphone, "
            f"may include other speakers]\n{text}")


def make_handler(ctx, cfg: dict):
    token_env = cfg["token_env"]
    expected = os.environ.get(token_env, "")

    class Receive(BaseHTTPRequestHandler):
        server_version = "watchlink-receiver/0.1"

        def reply(self, code: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self) -> bool:
            if not expected:      # token configured? no -> open, dev mode
                return True
            given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            return bool(given) and secrets.compare_digest(given, expected)

        def do_GET(self) -> None:
            self.reply(405, {"error": "method not allowed"})

        def do_POST(self) -> None:
            if self.path.split("?")[0].rstrip("/") not in ("/capture", ""):
                return self.reply(404, {"error": "not found"})
            if not self.authorized():
                return self.reply(401, {"error": "bad token"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                capture = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as exc:
                return self.reply(400, {"error": str(exc)})
            origin = {"platform": cfg["platform"], "chat_id": cfg["chat_id"]}
            ok = ctx.inject_message(injection_text(capture), role="user", origin=origin)
            log.info("injected capture %s into %s/%s: %s",
                     capture.get("id"), origin["platform"], origin["chat_id"], ok)
            return self.reply(200 if ok else 409, {"injected": bool(ok), "id": capture.get("id")})

        def log_message(self, *args) -> None:
            pass

    return Receive


def register(ctx) -> None:
    """Start a local listener that accepts captures and injects them into the chat."""
    cfg = settings(ctx)
    server = ThreadingHTTPServer((cfg["host"], int(cfg["port"])), make_handler(ctx, cfg))

    def serve() -> None:
        log.info("watchlink-receiver listening on http://%s:%s/capture (-> %s/%s)",
                 cfg["host"], cfg["port"], cfg["platform"], cfg["chat_id"])
        server.serve_forever()

    threading.Thread(target=serve, daemon=True, name="watchlink-receiver").start()
