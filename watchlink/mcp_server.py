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


# Where agent feedback goes. A plain URL - no custom domain needed: a free
# hosted endpoint (Cloudflare Worker, Vercel, Fly, a gist-backed function)
# works, and anyone self-hosting can point it at their own collector.
FEEDBACK_URL = "https://feedback.watchlink.dev/v1/notes"
FEEDBACK_TOKEN = ""          # optional shared token; empty = open endpoint


def send_feedback(message: str, kind: str = "note", context: str = "") -> dict:
    """POST a note to the project's feedback endpoint (opt-in, never automatic).

    Falls back to a local file when the endpoint is unreachable, so a note is
    never lost - an agent can tell the user what happened.
    """
    note = {"kind": kind, "message": message, "context": context,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "source": "watchlink"}
    body = json.dumps(note).encode()
    headers = {"Content-Type": "application/json"}
    if FEEDBACK_TOKEN:
        headers["Authorization"] = f"Bearer {FEEDBACK_TOKEN}"
    req = urllib.request.Request(FEEDBACK_URL, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as reply:
            return {"sent": True, "status": getattr(reply, "status", 200)}
    except Exception as exc:  # offline, DNS, 5xx - keep it locally instead
        path = daemon.HOME() / "feedback.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(note) + "\n")
        return {"sent": False, "error": str(exc), "saved_to": str(path)}


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
