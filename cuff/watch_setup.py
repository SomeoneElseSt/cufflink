"""wc watch-setup: build, sign and import the Apple Watch capture shortcut.

Drives Shortcuts with osascript - no manual clicking except the import dialog.
The API key is baked in at build time (the watch has no shell environment to
expand a variable), and is never printed.
"""

import subprocess
import sys
from pathlib import Path

CAPTURE = Path.home() / ".wrist" / "capture"

SHORTCUT_NAME = "cufflink"
# Shortcuts colours are ARGB ints; ink teal, matching the CLI's calm look.
ICON_COLOR_TEAL = 0xFF0F766E
# Glyph "mic.fill" from the SF Symbols set Shortcuts exposes.
ICON_GLYPH_MIC = 59498


def say(message: str) -> None:
    """Onboarding: tell the human what is about to happen and that it is safe."""
    print(f"cufflink: {message}", flush=True)
    subprocess.run(["osascript", "-e",
                    f'display notification "{message[:180]}" with title "cufflink setup"'],
                   capture_output=True)


def build(endpoint: str, key: str) -> Path:
    import plistlib

    CAPTURE.mkdir(parents=True, exist_ok=True)
    unsigned = CAPTURE / f"{SHORTCUT_NAME}.shortcut"
    actions = [
        {"WFWorkflowActionIdentifier": "is.workflow.actions.recordaudio",
         "WFWorkflowActionParameters": {
             "UUID": "11111111-1111-1111-1111-111111111111",
             "WFRecordingCompression": "Normal", "WFRecordingStart": "Immediately",
             "WFRecordingEnd": "On Tap"}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
         "WFWorkflowActionParameters": {
             "UUID": "22222222-2222-2222-2222-222222222222",
             "WFURL": endpoint, "WFHTTPMethod": "POST", "ShowHeaders": 1,
             "WFHTTPHeaders": {"X-API-Key": key, "Content-Type": "audio/mp4"},
             "WFHTTPBodyType": "File",
             "WFRequestVariable": {"WFSerializationType": "WFTextTokenAttachment",
                                   "Value": {"Type": "ActionOutput",
                                             "OutputUUID": "11111111-1111-1111-1111-111111111111",
                                             "OutputName": "Recorded Audio"}}}},
    ]
    plist = {"WFWorkflowActions": actions, "WFWorkflowClientVersion": "900",
             "WFWorkflowMinimumClientVersion": 900, "WFWorkflowIcon":
             {"WFWorkflowIconStartColor": ICON_COLOR_TEAL, "WFWorkflowIconGlyphNumber": ICON_GLYPH_MIC},
             "WFWorkflowInputContentItemClasses": [], "WFWorkflowName": SHORTCUT_NAME,
             "WFWorkflowOutputContentItemClasses": [], "WFWorkflowTypes": ["Watch"]}
    with open(unsigned, "wb") as fh:
        plistlib.dump(plist, fh)
    return unsigned


def sign_and_import(unsigned: Path) -> Path:
    signed = unsigned.with_name(f"{SHORTCUT_NAME}-signed.shortcut")
    subprocess.run(["shortcuts", "sign", "--mode", "anyone",
                    "--input", str(unsigned), "--output", str(signed)], check=True)
    subprocess.run(["open", str(signed)])
    return signed


def main(endpoint: str, key: str) -> int:
    if sys.platform != "darwin":
        print("watch-setup needs macOS", file=sys.stderr)
        return 2
    say("I'm going to build a Shortcut that records from your Watch and uploads "
        "it, then you'll see an import prompt - it's safe, it only records and "
        "sends to the address you configured.")
    signed = sign_and_import(build(endpoint, key))
    say(f"Import {signed.name} (named {SHORTCUT_NAME}, teal mic icon), then enable it for Apple Watch in Shortcuts.")
    return 0


if __name__ == "__main__":
    import os
    from cuff import config as C
    cfg = C.load()
    # The key must be the target daemon's, not this machine's: a shortcut
    # built on the Mac against the VM ingest gets 401 with the Mac key.
    key = os.environ.get("CUFF_INGEST_KEY") or (C.home_dir() / "key").read_text().strip()
    raise SystemExit(main(f"http://{cfg.server['host']}:{cfg.server['port']}/upload", key))
