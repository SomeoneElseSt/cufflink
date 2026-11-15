"""wc CLI - agent-first: every command is non-interactive and speaks --json.

    wc setup   [--json]   first run: dirs, key, config (asks the SETUP_QUESTIONS via flags)
    wc serve              run the daemon (ingest + watcher + router)
    wc status  [--json]   is it up, what is configured, queue depth
    wc config get|set     read/write one dotted key
    wc watch-setup        build + sign + import the watch shortcut (macOS)
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from watchlink import config as C


def out(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        for k, v in data.items():
            print(f"{k}: {v}")


def cmd_setup(args) -> int:
    C.home_dir().mkdir(parents=True, exist_ok=True)
    path = C.config_path()
    cfg = C.load(path)
    for pair in args.set or []:
        key, _, value = pair.partition("=")
        C.set_value(cfg, key, value)
    C.save(cfg, path)
    from watchlink import daemon
    key = daemon.api_key()
    pending = C.next_question(cfg)
    out({"config": str(path), "ingest_key_file": str(C.home_dir() / "key"),
         "ingest_url": f"http://{cfg.server['host']}:{cfg.server['port']}/upload",
         "next_question": pending["ask"] if pending else None}, args.json)
    return 0


def cmd_config(args) -> int:
    cfg = C.load()
    if args.action == "set":
        key, _, value = args.value.encode("utf-8").decode().partition("=") if False else args.value.partition("=")
        C.set_value(cfg, key, value)
        C.save(cfg)
    data = C.as_json(cfg)
    out(data if args.json else {"transcription": data["transcription"]}, args.json)
    return 0


def cmd_status(args) -> int:
    from watchlink import daemon
    cfg = C.load()
    queue = 0
    qdir = C.home_dir() / "queue"
    if qdir.exists():
        queue = len(list(qdir.glob("*.json")))
    rows = []
    if daemon.INDEX().exists():
        conn = daemon.db()
        rows = conn.execute(
            "select status, count(*) from recording group by status").fetchall()
        conn.close()
    handled = (512 if args.json else 0)
    known = [n for n in cfg.adapters] if False else []
    data = {
        "listening": f"http://{cfg.server['host']}:{cfg.server['port']}/upload",
        "daemon_pid": daemon.running(),
        "transcription": cfg.transcription.get("provider", "none"),
        "language": cfg.transcription.get("language", "auto"),
        "destinations": list(known),
        "queue_pending": queue,
        "recordings": dict(rows),
    }
    out(data, args.json)
    return 0


def cmd_serve(args) -> int:
    from watchlink import daemon
    if args.detach:
        pid = daemon.start_detached()
        out({"detached": True, "pid": pid, "log": str(daemon.HOME() / "watchlink.log")}, args.json)
        return 0
    daemon.serve()
    return 0


def cmd_stop(args) -> int:
    from watchlink import daemon
    stopped = daemon.stop_running()
    out({"stopped": stopped}, args.json)
    return 0


def cmd_watch_setup(args) -> int:
    if sys.platform != "darwin":
        print("watch-setup needs macOS (it drives Shortcuts with osascript)", file=sys.stderr)
        return 2
    script = Path(__file__).with_name("build_capture_shortcut.py")
    done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    if done.returncode != 0:
        print(done.stderr[:500], file=sys.stderr)
        return 1
    signed = Path(done.stdout.strip().splitlines()[-1].split("signed: ")[-1]) if "signed:" in done.stdout else None
    if signed and signed.exists():
        subprocess.run(["open", str(signed)])
    print("Import the shortcut, then enable it for Apple Watch in Shortcuts.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="watchlink", description="wrist capture for agents")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup", help="first run: dirs, key, config")
    p.add_argument("--json", action="store_true")
    p.add_argument("--set", action="append", metavar="key=value")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("serve", help="run the daemon")
    p.add_argument("--detach", action="store_true",
                   help="keep running after this shell/session exits")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("stop", help="stop a detached daemon")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("status", help="is it up, what is configured")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("config", help="read or write one key")
    p.add_argument("action", choices=["get", "set"])
    p.add_argument("value", nargs="?", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("watch-setup", help="build + import the watch shortcut")
    p.set_defaults(func=cmd_watch_setup)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
