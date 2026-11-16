"""watchlink-receiver: a Hermes plugin that puts a wrist capture into YOUR chat.

The flow it completes:

    Watch -> watchlink transcribes -> watchlink route -> this plugin -> this chat

Why a plugin and not a webhook: Hermes's inject_message API has no public HTTP
endpoint - the plugin must already know which chat to target. So the plugin
learns the chat from the user's own messages (the post_gateway_admission hook
hands it the session key of every admitted human message) and injects by that
key. No session id is configured, nothing is hardcoded to one install.

Safety: a transcript is not necessarily an instruction from you - the watch
microphone hears other people too. Each injected turn is prefixed with a marker
naming the capture id, so the agent can tell captured audio apart from something
you typed.

Config, under plugins.entries.watchlink-receiver.settings in config.yaml:

    host: 127.0.0.1
    port: 8770
    token_env: WATCHLINK_RECEIVER_TOKEN   # bearer token the sender must present
    dm_only: true                        # learn only the user's DM, not groups
"""

import json
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("watchlink.receiver")

DEFAULTS = {"host": "127.0.0.1", "port": "8770",
            "token_env": "WATCHLINK_RECEIVER_TOKEN", "dm_only": "true"}

ROUTE_STATE = "route"      # the learned {"session_key", "platform"} in ctx.state


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


def remember_route(ctx, dm_only: bool):
    """Learn which session the user actually talks to, from their own messages."""
    def on_admission(session_key, platform, source, text, **kwargs):
        source = source or {}
        if dm_only and source.get("chat_type") not in (None, "dm", "private"):
            return None                      # a group chat is not the user's chat
        known = ctx.state.get(ROUTE_STATE) or {}
        if known.get("session_key") == session_key:
            return None                      # already known: no write per message
        ctx.state.set(ROUTE_STATE, {"session_key": session_key, "platform": platform})
        log.info("learned route: %s on %s", session_key, platform)
        return None                          # never consume the message

    ctx.register_hook("post_gateway_admission", on_admission)


def make_handler(ctx, cfg: dict):
    expected = os.environ.get(cfg["token_env"], "")

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
            if not expected:                 # no token configured -> open local listener
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
            route = ctx.state.get(ROUTE_STATE) or {}
            if not route.get("session_key"):
                # Nothing to target yet. 503 so watchlink keeps it queued and retries.
                self.reply(503, {"injected": False, "id": capture.get("id"),
                                 "error": "no route yet - message your agent once from the chat you want captures in"})
                return log.warning("no route yet: dropped nothing, answered 503")
            ok = ctx.inject_message(injection_text(capture), role="user",
                                    session_key=route["session_key"])
            log.info("injected capture %s into %s: %s", capture.get("id"), route["session_key"], ok)
            return self.reply(200 if ok else 409, {"injected": bool(ok), "id": capture.get("id")})

        def log_message(self, *args) -> None:
            pass

    return Receive


def register(ctx) -> None:
    """Learn the user's session from their messages, then accept captures."""
    cfg = settings(ctx)
    remember_route(ctx, dm_only=str(cfg.get("dm_only", "true")).lower() == "true")
    server = ThreadingHTTPServer((cfg["host"], int(cfg["port"])), make_handler(ctx, cfg))

    def serve() -> None:
        log.info("watchlink-receiver listening on http://%s:%s/capture (injects into the learned session)",
                 cfg["host"], cfg["port"])
        server.serve_forever()

    threading.Thread(target=serve, daemon=True, name="watchlink-receiver").start()
