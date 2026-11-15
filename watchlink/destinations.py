"""Destinations for wc: deliver a classified capture to an agent.

Two real targets, one code path:
  hermes   -> POST <url>  with Standard Webhooks HMAC headers
  openclaw -> POST <url>/hooks/agent  with Authorization: Bearer <token>

Setup is meant to be: ask the agent to make the webhook, it hands back a URL
(+ secret or token), then `wc config set destinations.hermes.url ...` - so the
config is the only thing that changes per agent.
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib import request as urlrequest

QUEUE_DIR = Path(os.environ.get("WATCHLINK_HOME", Path.home() / ".wrist")) / "queue"

# auth scheme -> headers. Schemes are data: adding one is a dict entry.
SCHEMES = {
    "standard-webhooks": "signed",
    "bearer": "token",
    "none": "plain",
}


@dataclass
class Destination:
    name: str
    url: str
    scheme: str = "bearer"          # standard-webhooks | bearer | none
    secret_env: str = ""           # HMAC secret for standard-webhooks (whsec_...)
    token_env: str = ""            # bearer token
    headers: dict = field(default_factory=dict)
    path_suffix: str = ""          # e.g. "/hooks/agent" for openclaw


def sign_standard_webhooks(secret: str, msg_id: str, timestamp: int, body: bytes) -> dict:
    """webhook-id / webhook-timestamp / webhook-signature over {id}.{ts}.{body}."""
    raw = secret.removeprefix("whsec_")
    # the secret is used as raw bytes: decoding it as base64 produced
    # "Invalid signature" from the live Hermes gateway, raw bytes were accepted
    key = raw.encode()
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    import base64
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": "v1," + base64.b64encode(digest).decode(),
    }


def build_headers(dest: Destination, msg_id: str, body: bytes) -> dict:
    headers = {"Content-Type": "application/json", **dest.headers}
    kind = SCHEMES.get(dest.scheme, "token")
    if kind == "signed":
        secret = os.environ.get(dest.secret_env, "")
        headers.update(sign_standard_webhooks(secret, msg_id, int(time.time()), body))
    elif kind == "token":
        headers["Authorization"] = f"Bearer {os.environ.get(dest.token_env, '')}"
    headers["Idempotency-Key"] = msg_id        # openclaw honours this one natively
    return headers


def payload(capture: dict) -> bytes:
    """Flat, predictable keys - agents template them by dot-notation."""
    body = {
        "id": capture["id"],
        "ts": capture["ts"],
        "text": capture.get("text", ""),
        "message": capture.get("text", ""),   # agents template {message} as often as {text}
        "route": capture.get("route", ""),
        "media": capture.get("audio_path", ""),
    }
    return json.dumps(body, separators=(",", ":")).encode()


def deliver(dest: Destination, capture: dict, timeout: int = 15, retries: int = 3) -> dict:
    msg_id = str(uuid.uuid4())
    body = payload(capture)
    url = dest.url.rstrip("/") + dest.path_suffix
    headers = build_headers(dest, msg_id, body)
    last = ""
    for attempt in range(1, retries + 1):
        req = urlrequest.Request(url, data=body, headers=headers, method="POST")
        try:
            with urlrequest.urlopen(req, timeout=timeout) as reply:
                return {"ok": True, "status": reply.status, "id": msg_id, "attempt": attempt}
        except Exception as exc:               # network or non-2xx
            last = str(exc)[:200]
            time.sleep(min(2 ** attempt, 10))
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    (QUEUE_DIR / f"{msg_id}.json").write_text(json.dumps({"dest": dest.name, "capture": capture, "headers": headers}))
    return {"ok": False, "error": last, "queued": msg_id}


# What the agent is told when it asks an agent to create the webhook for you:
SETUP_HINTS = {
    "hermes": "Ask Hermes to create an incoming webhook and give you its URL and secret. "
              "Then: wc config set destinations.hermes.url <url> "
              "&& wc config set destinations.hermes.scheme standard-webhooks "
              "&& wc config set destinations.hermes.secret_env HERMES_WEBHOOK_SECRET",
    "openclaw": "Ask OpenClaw for its hooks token and gateway URL. "
                "Then: wc config set destinations.openclaw.url <gateway url> "
                "&& wc config set destinations.openclaw.path_suffix /hooks/agent "
                "&& wc config set destinations.openclaw.token_env OPENCLAW_HOOKS_TOKEN",
}
