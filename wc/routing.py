"""Routing: where a capture goes. Optional and pluggable.

Three strategies, chosen by [routing] type in the config:

  naive      first [[routes]] whose match words appear in the text wins
  classifier an OpenAI-compatible chat endpoint picks one route label
  llm        same endpoint, but the model sees the routes' `about` text

Rules that matter:
- No routes configured -> nothing is routed. The capture stays in the index,
  readable through MCP and the CLI. That is the default case.
- A route whose dest is not a configured destination is an ERROR at load,
  not a silent drop: a typo in the routing table must be loud.
- If a model-based strategy fails (endpoint down, garbage label), fall back to
  naive matching rather than losing the capture.
"""

import json
from urllib import request as urlrequest

ROUTE_TYPES = ("naive", "classifier", "llm")

CLASSIFY_PROMPT = (
    "You route a short spoken note to one destination. "
    "Answer with exactly one label from this list, nothing else: {labels}.\n"
    "If none fits, answer: none.\n"
    "Labels mean: {about}\nNote: {text}"
)


def validate(routes: list, destinations: dict) -> None:
    """Loud on typos: every route must point at a real destination."""
    for rule in routes:
        dest = rule.get("dest", "")
        if dest and dest not in destinations:
            raise ValueError(f"route {rule.get('name', '?')!r} points at unknown destination {dest!r} "
                             f"(configured: {sorted(destinations)})")
        if not dest:
            raise ValueError(f"route {rule.get('name', '?')!r} has no dest")


def naive(routes: list, text: str) -> tuple[str, str]:
    """First matching rule wins; returns (dest, route name)."""
    low = text.lower()
    for rule in routes:
        match = rule.get("match", {})
        words = match.get("any", [])
        if words and any(w.lower() in low for w in words):
            return rule.get("dest", ""), rule.get("name", "")
        if match.get("always"):
            return rule.get("dest", ""), rule.get("name", "")
    return "", ""


def ask_model(cfg, text: str) -> str:
    """One chat completion, OpenAI-compatible. Returns the label or ''."""
    spec = cfg.routing
    base = (spec.get("base_url") or "").rstrip("/")
    if not base:
        return ""
    labels = [r.get("name", "") for r in cfg.routes if r.get("name")] + ["none"]
    about = "; ".join(f"{r.get('name')}: {r.get('about', r.get('name'))}" for r in cfg.routes)
    prompt = CLASSIFY_PROMPT.format(labels=", ".join(labels), about=about, text=text)
    body = json.dumps({"model": spec.get("model", ""), "temperature": 0,
                      "messages": [{"role": "user", "content": prompt}]}).encode()
    headers = {"Content-Type": "application/json"}
    key = __import__("os").environ.get(spec.get("api_key_env") or "", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urlrequest.Request(base + "/chat/completions", data=body, headers=headers, method="POST")
    with urlrequest.urlopen(req, timeout=30) as reply:
        content = json.loads(reply.read())["choices"][0]["message"]["content"].strip()
    return content.split()[0].strip(".,\"'") if content else ""


def route(cfg, text: str) -> tuple[str, str]:
    """(dest, route name) for a transcript; ('', '') when nothing routes."""
    routes = getattr(cfg, "routes", [])
    if not routes:
        return "", ""
    kind = getattr(cfg, "routing", {}).get("type", "naive")
    if kind in ("classifier", "llm"):
        try:
            label = ask_model(cfg, text)
        except Exception:
            label = ""
        by_name = {r.get("name"): r for r in routes}
        if label in by_name:
            return by_name[label].get("dest", ""), label
        if label and label != "none":
            return "", ""
    return naive(routes, text)
