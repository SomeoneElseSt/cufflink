"""The wc daemon: hilariously light.

One process, one thread per concern, stdlib only:
  ingest   POST /upload  -> file on disk + row in index.db
  watch    poll index.db for rows without a transcript -> transcribe -> route -> deliver

No framework, no async magic: a ThreadingHTTPServer plus a loop with a 1 s sleep.
"""

import json
import os
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request as urlrequest

from cuff import config as C
from cuff import destinations as D

def HOME() -> Path:  # noqa: N802 - follows CUFF_HOME at call time
    return C.home_dir()


def KEY_FILE() -> Path: return HOME() / "key"
def INDEX() -> Path: return HOME() / "index.db"
def RECORDINGS() -> Path: return HOME() / "recordings"
POLL_SECONDS = 1.0
MAX_BYTES = 200 * 1024 * 1024

SCHEMA = """
create table if not exists recording (
  id text primary key, ts text, bytes int, path text, transcript text,
  route text, status text, error text
);
"""


# ---- storage ---------------------------------------------------------------
def db() -> sqlite3.Connection:
    HOME().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(INDEX(), timeout=10)
    conn.execute(SCHEMA)
    return conn


def api_key() -> str:
    if not KEY_FILE().exists():
        KEY_FILE().write_text(secrets.token_urlsafe(32))
        KEY_FILE().chmod(0o600)
    return KEY_FILE().read_text().strip()


def save_recording(body: bytes, name: str) -> dict:
    RECORDINGS().mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    rid = f"{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
    path = RECORDINGS() / f"{rid}-{name}.m4a"
    path.write_bytes(body)
    path.chmod(0o600)
    conn = db()
    conn.execute("insert into recording (id, ts, bytes, path, status) values (?,?,?,?,?)",
                 (rid, stamp, len(body), str(path), "new"))
    conn.commit()
    conn.close()
    return {"ok": True, "id": rid, "bytes": len(body)}


# ---- ingest ---------------------------------------------------------------
def make_handler(key: str):
    class Upload(BaseHTTPRequestHandler):
        server_version = "wc/0.1"

        def reply(self, code: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self.reply(405, {"error": "method not allowed"})

        do_PUT = do_GET
        do_DELETE = do_GET

        def do_POST(self) -> None:
            if self.path.split("?")[0].rstrip("/") not in ("/upload", ""):
                return self.reply(404, {"error": "not found"})
            given = self.headers.get("X-API-Key", "")
            if not given or not secrets.compare_digest(given, key):
                return self.reply(401, {"error": "bad api key"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return self.reply(400, {"error": "empty body"})
            if length > MAX_BYTES:
                return self.reply(413, {"error": "too large"})
            body = self.rfile.read(length)
            name = self.path.split("name=")[1].split("&")[0] if "name=" in self.path else "recording"
            name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)[:60] or "recording"
            self.reply(201, save_recording(body, name))

        def log_message(self, *args) -> None:
            pass

    return Upload


# ---- the loop: transcribe -> route -> deliver ------------------------------
def transcribe(cfg: C.Config, path: str) -> str:
    from wc.transcribe import transcribe_file   # kept out of the hot import path
    return transcribe_file(cfg, path)


def route(cfg: C.Config, text: str) -> str:
    """Route a transcript through the configured strategy (optional)."""
    from cuff import routing
    routing.validate(cfg.routes, cfg.destinations)
    return routing.route(cfg, text)[0]


def deliver(cfg: C.Config, dest_name: str, capture: dict) -> dict:
    spec = cfg.destinations.get(dest_name)
    if not spec:
        return {"ok": False, "error": f"no destination {dest_name}"}
    dest = D.Destination(
        name=dest_name,
        url=spec.get("url", ""),
        scheme=spec.get("scheme", "bearer"),
        secret_env=spec.get("secret_env", ""),
        token_env=spec.get("token_env", ""),
        headers=spec.get("headers", {}),
        path_suffix=spec.get("path_suffix", ""),
    )
    return D.deliver(dest, capture)


def worker(cfg_path: Path, stop: threading.Event) -> None:
    while not stop.is_set():
        cfg = C.load(cfg_path)
        conn = db()
        row = conn.execute("select id, ts, path from recording where status='new' limit 1").fetchone()
        if row is None:
            conn.close()
            stop.wait(POLL_SECONDS)
            continue
        rid, ts, path = row
        conn.execute("update recording set status='working' where id=?", (rid,))
        conn.commit()
        conn.close()
        status, text, error = "done", "", ""
        try:
            text = transcribe(cfg, path)
            dest = route(cfg, text)
            if dest:
                out = deliver(cfg, dest, {"id": rid, "ts": ts, "text": text,
                                          "route": dest, "audio_path": path})
                if not out.get("ok"):
                    status, error = "delivery_failed", out.get("error", "")
        except Exception as exc:
            status, error = "failed", str(exc)[:300]
        conn = db()
        conn.execute("update recording set status=?, transcript=?, route=?, error=? where id=?",
                     (status, text, route(cfg, text), error, rid))
        conn.commit()
        conn.close()


# ---- lifecycle -------------------------------------------------------------
def pid_file() -> Path:
    return HOME() / "cuff.pid"


def running() -> int | None:
    """The daemon's pid if it is alive, else None (stale files are cleaned)."""
    path = pid_file()
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        path.unlink(missing_ok=True)
        return None


def stop_running() -> bool:
    pid = running()
    if pid is None:
        return False
    os.kill(pid, 15)
    for _ in range(50):
        if running() is None:
            return True
        time.sleep(0.1)
    os.kill(pid, 9)
    return True


def start_detached(cfg_path: Path | None = None) -> int:
    """Double-fork so an agent's shell can exit (or its session close) and the
    daemon keeps running. logs to ~/.wrist/cuff.log, pid in ~/.wrist/cuff.pid."""
    existing = running()
    if existing is not None:
        return existing
    HOME().mkdir(parents=True, exist_ok=True)
    log = open(HOME() / "cuff.log", "ab")
    first = os.fork()
    if first > 0:
        os.waitpid(first, 0)
        for _ in range(50):
            pid = running()
            if pid is not None:
                return pid
            time.sleep(0.1)
        return 0
    os.setsid()
    second = os.fork()
    if second > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    pid_file().write_text(str(os.getpid()))
    try:
        serve(cfg_path)
    finally:
        pid_file().unlink(missing_ok=True)
    os._exit(0)


def serve(cfg_path: Path | None = None) -> None:
    cfg = C.load(cfg_path)
    key = api_key()
    stop = threading.Event()
    threading.Thread(target=worker, args=(cfg_path, stop), daemon=True).start()
    server = ThreadingHTTPServer((cfg.server["host"], int(cfg.server["port"])), make_handler(key))
    print(f"wc serving on http://{cfg.server['host']}:{cfg.server['port']}/upload", flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()


def wake(_: str = "") -> None:
    """Nudge the worker immediately (used after an upload)."""
    with urlrequest.urlopen(urlrequest.Request("http://127.0.0.1:1/", method="POST")) as _r:
        pass
