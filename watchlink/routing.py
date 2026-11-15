"""Routing: where a capture goes. Optional and pluggable.

Two model-based strategies over the same list of route labels, differing in
HOW they are asked to answer:

  classifier   System 1 - one fast pass, constrained to the enum
               (response_format json_schema / enum), no reasoning
  llm          System 2 - reasons first, then answers; the label is parsed
               out of prose and RETRIED (with the error fed back) when invalid
  naive        no model: first rule whose match words appear

Any OpenAI-compatible chat endpoint can serve either: a local decision model,
or a hosted one. Nothing here knows the vendor - only base_url + model.
"""

import json
import os
from urllib import request as urlrequest

ROUTE_TYPES = ("naive", "classifier", "llm")
LLM_ATTEMPTS = 3

CLASSIFY_PROMPT = (
    "Route this spoken note to exactly one destination.\n"
    "Allowed labels: {labels}\n"
    "What each label means: {about}\n"
    "Note: {text}\n"
    "Answer with the label only."
)

REASON_PROMPT = (
    "You route a short spoken note to one destination.\n"
    "Labels and what they mean: {about}\n"
    "Think briefly about what the note is asking for, then finish with a "
    "final line of exactly: LABEL: <one of {labels}>\n"
    "Note: {text}"
)


def validate(routes: list, destinations: dict) -> None:
    """Loud on typos: every route must point at a real destination."""
    for rule in routes:
        name = rule.get("name", "?")
        dest = rule.get("dest", "")
        if not dest:
            raise ValueError(f"route {name!r} has no dest")
        if dest not in destinations:
            raise ValueError(f"route {name!r} points at unknown destination {dest!r} "
                             f"(configured: {sorted(destinations)})")


def naive(routes: list, text: str) -> tuple[str, str]:
    low = text.lower()
    for rule in routes:
        match = rule.get("match", {})
        words = match.get("any", [])
        if words and any(w.lower() in low for w in words):
            return rule.get("dest", ""), rule.get("name", "")
        if match.get("always"):
            return rule.get("dest", ""), rule.get("name", "")
    return "", ""


# ---- the one HTTP call, two request shapes --------------------------------
def chat(spec: dict, body: dict, timeout: int = 30) -> str:
    """POST to <base_url>/chat/completions; returns the message content."""
    base = (spec.get("base_url") or "").rstrip("/")
    if not base:
        raise ValueError("routing.base_url is not configured")
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(spec.get("api_key_env") or "", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urlrequest.Request(base + "/chat/completions",
                             data=json.dumps(body).encode(), headers=headers, method="POST")
    with urlrequest.urlopen(req, timeout=timeout) as reply:
        return json.loads(reply.read())["choices"][0]["message"]["content"].strip()


def classifier_label(spec: dict, labels: list[str], about: str, text: str) -> str:
    """System 1: constrained to the enum, one shot, no reasoning."""
    body = {
        "model": spec.get("model", ""),
        "temperature": 0,
        "messages": [{"role": "user", "content": CLASSIFY_PROMPT.format(
            labels=", ".join(labels), about=about, text=text)}],
        # enum constraint; providers that ignore it still get a one-word ask
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "route", "strict": True,
            "schema": {"type": "object", "properties": {
                "label": {"type": "string", "enum": labels}},
                "required": ["label"]}}},
    }
    content = chat(spec, body)
    try:
        return json.loads(content)["label"]
    except (json.JSONDecodeError, KeyError):
        return content.split()[0].strip(".,\"'") if content else ""


def llm_label(spec: dict, labels: list[str], about: str, text: str) -> str:
    """System 2: reason, then answer; retry with the error fed back."""
    messages = [{"role": "user", "content": REASON_PROMPT.format(
        about=about, labels=", ".join(labels), text=text)}]
    last_error = ""
    for _ in range(LLM_ATTEMPTS):
        content = chat(spec, {"model": spec.get("model", ""), "temperature": 0,
                              "messages": messages})
        candidate = ""
        for line in reversed(content.splitlines()):
            if "LABEL:" in line:
                candidate = line.split("LABEL:", 1)[1].strip().strip(".,\"'")
                break
        if candidate in labels:
            return candidate
        last_error = f"your answer {candidate!r} is not one of {labels}"
        messages += [
            {"role": "assistant", "content": content},
            {"role": "user", "content": f"That is not valid: {last_error}. "
                                        f"Finish with exactly: LABEL: <one of {labels}>"},
        ]
    return ""


def route(cfg, text: str) -> tuple[str, str]:
    """(dest, route name); ('', '') when nothing routes.

    With no routes at all, everything goes to the first destination so a
    capture is never silently stranded - that is the "no classifier yet"
    case, and it is what makes the pipeline testable end to end.
    """
    routes = getattr(cfg, "routes", [])
    if not routes:
        destinations = getattr(cfg, "destinations", {})
        first = next(iter(destinations), "")
        return (first, "default") if first else ("", "")
    by_name = {r.get("name", ""): r for r in routes if r.get("name")}
    spec = getattr(cfg, "routing", {})
    kind = spec.get("type", "naive")
    # decision tree, in order: naive first (free, no model), then the model.
    first_try = naive(routes, text)
    if first_try[0] and kind == "naive":
        return first_try
    if kind in ("classifier", "llm") and by_name:
        labels = list(by_name) + ["none"]
        about = "; ".join(f"{name}: {r.get('about', name)}" for name, r in by_name.items())
        try:
            label = (classifier_label(spec, labels, about, text) if kind == "classifier"
                     else llm_label(spec, labels, about, text))
        except Exception:
            label = ""
        if label in by_name:
            return by_name[label].get("dest", ""), label
        if label == "none":
            return "", ""
    return first_try
