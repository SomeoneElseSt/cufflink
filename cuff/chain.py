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
    """(result, provider name that answered, errors from the ones that did not).

    The declaration is the order - inline entries carry a `name`:

        chain = [
          { name = "mac-local", base_url = "http://192.168.1.20:8000/v1" },
          { name = "deepgram", base_url = "https://api.deepgram.com/v1", api_key_env = "DEEPGRAM_API_KEY" },
          { name = "none" },
        ]

    Read top to bottom: tried in that order. A plain string entry
    (`chain = ["mac-local", "deepgram"]`) still works when the full spec lives
    in [transcription.providers.<name>].
    """
    errors: list[str] = []
    providers = section.get("providers") or {}
    for entry in section.get("chain") or []:
        if isinstance(entry, dict):
            name = entry.get("name", "")
            spec = {k: v for k, v in entry.items() if k != "name"}
            spec = {**providers.get(name, {}), **spec}
        else:
            name, spec = entry, providers.get(entry, {})
        if name == "none":
            return None, "none", errors
        try:
            return call(name, spec), name, errors
        except Exception as exc:                       # any provider failure -> next
            errors.append(f"{name}: {str(exc)[:160]}")
    return None, "", errors


def single(section: dict) -> tuple[dict, str]:
    """No chain: the flat provider fields on the section itself."""
    return section, section.get("provider", "")
