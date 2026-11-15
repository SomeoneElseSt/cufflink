"""MCP over HTTP with a shared token - the remote case (watchlink on a VM).

    WATCHLINK_MCP_TOKEN=<token> python -m watchlink.mcp_http --http --host 0.0.0.0 --port 8767

Every request must carry `Authorization: Bearer <token>`. The token lives in
~/.wrist/mcp_token (0600) when not given in the environment, created on first
use - the same file `watchlink setup` reads to write the local MCP config.
"""

import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from watchlink import config as C


def mcp_token() -> str:
    given = os.environ.get("WATCHLINK_MCP_TOKEN", "")
    if given:
        return given
    path = C.home_dir() / "mcp_token"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32))
        path.chmod(0o600)
    return path.read_text().strip()


def serve_http(host: str = "127.0.0.1", port: int = 8767) -> None:
    from watchlink.mcp_server import handle

    token = mcp_token()

    class H(BaseHTTPRequestHandler):
        server_version = "watchlink-mcp/0.1"

        def authorized(self) -> bool:
            given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            return bool(given) and secrets.compare_digest(given, token)

        def reply(self, code: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self.reply(405, {"error": "method not allowed"})

        def do_POST(self) -> None:
            if not self.authorized():
                return self.reply(401, {"error": "bad token"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                request = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as exc:
                return self.reply(400, {"error": str(exc)})
            reply = handle(request)
            self.reply(200, reply or {})

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer((host, port), H)
    print(f"watchlink mcp (http, token auth) on http://{host}:{port}", flush=True)
    server.serve_forever()


def main(argv: list[str]) -> int:
    host, port = "127.0.0.1", 8767
    for flag, value in zip(argv[::2], argv[1::2]):
        if flag == "--host":
            host = value
        elif flag == "--port":
            port = int(value)
    serve_http(host, port)
    return 0


if __name__ == "__main__":
    if "--http" in sys.argv:
        rest = [a for a in sys.argv[1:] if a != "--http"]
        sys.exit(main(rest))
    from watchlink.mcp_server import serve_stdio
    serve_stdio()
