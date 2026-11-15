"""wc MCP server: read captures back, and get woken when a tagged one lands.

Two transports, same code: stdio for a laptop, HTTP for a box you reach remotely.
One tool and one resource type - resolution is what agents actually need:

    list_captures  { route?, since?, limit? } -> [{id, ts, route, text, media}]
    get_capture    { id }                     -> {id, ts, route, text, media}
    read_resource  recordings://latest        -> the newest capture

Push: a client that subscribes to `recordings://<route>` is notified
(`notifications/resources/updated`) when a matching capture is stored - that is
how an agent wakes up on "messages tagged X" without polling.

Stdlib only: JSON-RPC over stdin/stdout or a tiny HTTP POST endpoint.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

from watchlink import daemon


def captures(route: str = "", since: str = "", limit: int = 20) -> list[dict]:
    conn = daemon.db()
    sql = "select id, ts, route, transcript, path, status from recording where 1=1"
    args: list = []
    if route:
        sql += " and route = ?"
        args.append(route)
    if since:
        sql += " and ts >= ?"
        args.append(since)
    sql += " order by ts desc limit ?"
    args.append(int(limit))
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [{"id": r[0], "ts": r[1], "route": r[2], "text": r[3] or "",
             "media": r[4], "status": r[5]} for r in rows]


def get_capture(rid: str) -> dict | None:
    conn = daemon.db()
    row = conn.execute("select id, ts, route, transcript, path, status from recording where id = ?",
                       (rid,)).fetchone()
    conn.close()
    if row is None:
        return None
    return {"id": row[0], "ts": row[1], "route": row[2], "text": row[3] or "",
            "media": row[4], "status": row[5]}


# Where agent feedback goes: GitHub issues on the project repo. There is no
# anonymous write, so the token is the user's own (GITHUB_TOKEN, or the token
# `gh auth token` prints) - never shipped with the package.
FEEDBACK_REPO = "SomeoneElseSt/watchlink"
FEEDBACK_API = f"https://api.github.com/repos/{FEEDBACK_REPO}/issues"


def feedback_token() -> str:
    given = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if given:
        return given
    try:  # the user already logged in with gh
        done = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def send_feedback(message: str, kind: str = "note", context: str = "") -> dict:
    """Open a GitHub issue on the project repo (opt-in, never automatic).

    Needs a token: GITHUB_TOKEN / GH_TOKEN, or a `gh` login. Without one the
    note is kept locally so it is not lost, and the caller is told why.
    """
    title = f"[{kind}] {message[:70]}"
    body = f"{message}\n\n---\ncontext: {context}\nwhen: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\nsource: watchlink"
    token = feedback_token()
    note = {"kind": kind, "message": message, "context": context,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "source": "watchlink"}
    if not token:
        return {"sent": False, "error": "no GitHub token (set GITHUB_TOKEN or run gh auth login)",
                "saved_to": _save_locally(note)}
    payload = json.dumps({"title": title, "body": body}).encode()
    req = urllib.request.Request(FEEDBACK_API, data=payload, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}", "User-Agent": "watchlink"})
    try:
        with urllib.request.urlopen(req, timeout=15) as reply:
            made = json.loads(reply.read())
            return {"sent": True, "issue": made.get("html_url"), "number": made.get("number")}
    except Exception as exc:  # offline, 401, rate limited - keep it locally
        return {"sent": False, "error": str(exc), "saved_to": _save_locally(note)}


def _save_locally(note: dict) -> str:
    path = daemon.HOME() / "feedback.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(note) + "\n")
    return str(path)


TOOLS = {
    "list_captures": {
        "description": "List captures (newest first). Filter by route or timestamp.",
        "input": {"route": "string", "since": "string", "limit": "integer"},
    },
    "get_capture": {
        "description": "Read one capture by id.",
        "input": {"id": "string"},
    },
    "send_feedback": {
        "description": "Send a note to the watchlink maintainers (bug, idea, confusion). "
                       "Opt-in: call it only when the user wants feedback sent.",
        "input": {"message": "string", "kind": "string", "context": "string"},
    },
}


def handle(request: dict) -> dict | None:
    """One JSON-RPC request in, one response out (None for notifications)."""
    method = request.get("method", "")
    params = request.get("params") or {}
    rid = request.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-06-18",
            "serverInfo": {"name": "watchlink", "version": "0.1"},
            "capabilities": {"tools": {}, "resources": {"subscribe": True}}}}
    if method == "tools/list":
        tools = [{"name": name, "description": spec["description"], "inputSchema": {
            "type": "object",
            "properties": {k: {"type": t} for k, t in spec["input"].items()}}} for name, spec in TOOLS.items()]
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
    if method == "tools/call":
        name = params.get("name")
        kwargs = params.get("arguments") or {}
        if name == "list_captures":
            result = captures(**{k: v for k, v in kwargs.items() if k in ("route", "since", "limit")})
        elif name == "get_capture":
            result = get_capture(kwargs.get("id", ""))
        elif name == "send_feedback":
            result = send_feedback(kwargs.get("message", ""), kwargs.get("kind", "note"),
                                   kwargs.get("context", ""))
        else:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text",
                "text": json.dumps(result, indent=2)}]}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": [
            {"uri": "recordings://latest", "name": "latest capture", "mimeType": "application/json"}]}}
    if method == "resources/read":
        uri = params.get("uri", "recordings://latest")
        if uri.startswith("recordings://latest"):
            latest = (captures(limit=1) or [None])[0]
            return {"jsonrpc": "2.0", "id": rid, "result": {"contents": [
                {"uri": uri, "mimeType": "application/json", "text": json.dumps(latest)}]}}
        return {"jsonrpc": "2.0", "id": rid, "result": {"contents": []}}
    if method == "resources/subscribe":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}}


def notify_latest(route: str) -> dict:
    """What the client receives when a matching capture is stored."""
    return {"jsonrpc": "2.0", "method": "notifications/resources/updated",
            "params": {"uri": f"recordings://{route}"}}


def serve_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except json.JSONDecodeError as exc:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


def serve_http(host: str = "127.0.0.1", port: int = 8766) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            reply = handle(json.loads(body or b"{}"))
            data = json.dumps(reply or {}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:
            pass

    ThreadingHTTPServer((host, port), H).serve_forever()


if __name__ == "__main__":
    if "--http" in sys.argv:
        serve_http()
    else:
        serve_stdio()
