"""One fallback chain for every step: N providers, tried in order.

    [transcription]
    chain = ["mac-local", "deepgram", "none"]

    [transcription.providers.mac-local]
    base_url = "http://192.168.1.20:8000/v1"

    [transcription.providers.deepgram]
    base_url = "https://api.deepgram.com/v1"
    api_key_env = "DEEPGRAM_API_KEY"

A step is an ordered list of named providers; the first that succeeds wins and
its name is returned so the index can record `transcribe_via = "deepgram"`.
"none" is a terminal entry meaning "stop, keep the audio, write the
placeholder". If every entry fails, the step returns nothing and the audio is
kept - a broken chain never loses a recording.
"""

from typing import Any, Callable


def resolve(section: dict, call: Callable[[str, dict], Any]) -> tuple[Any, str, list[str]]:
    """(result, provider name that answered, errors from the ones that did not)."""
    errors: list[str] = []
    for name in section.get("chain") or []:
        if name == "none":
            return None, "none", errors
        spec = (section.get("providers") or {}).get(name, {})
        try:
            return call(name, spec), name, errors
        except Exception as exc:                       # any provider failure -> next
            errors.append(f"{name}: {str(exc)[:160]}")
    return None, "", errors


def single(section: dict) -> tuple[dict, str]:
    """No chain: the flat provider fields on the section itself."""
    return section, section.get("provider", "")
