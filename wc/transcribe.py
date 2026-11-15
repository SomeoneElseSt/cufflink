"""Transcription: ONE function for every provider.

The contract is the OpenAI audio-transcriptions shape - POST multipart with
`file` and `model`, `Authorization: Bearer <key>`, JSON `{"text": ...}` - which
most ASR services and local servers implement. A config adapter can override
`path` and `response_path` for a provider that deviates (see config.ADAPTERS).

Local whisper is the same function with `base_url` empty: it decodes with ffmpeg
and runs faster-whisper in-process.
"""

import json
import subprocess
import uuid
from pathlib import Path
from urllib import request as urlrequest


def _dig(data: dict, path: list):
    for step in path:
        data = data[step]
    return data


def _multipart(fields: dict, file_path: str, file_field: str = "file") -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode()
    name = Path(file_path).name
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
             f"filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
    body += Path(file_path).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def whisper_local(file_path: str, model: str = "small", model_dir: str = "") -> str:
    """In-process whisper; ffmpeg decodes so an old PyAV cannot break the path."""
    import numpy as np
    from faster_whisper import WhisperModel

    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", file_path, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True, timeout=300,
    ).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    whisper = WhisperModel(model_dir or model, device="cpu", compute_type="int8")
    segments, _ = whisper.transcribe(audio, beam_size=5, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe_file(cfg, file_path: str) -> str:
    spec = cfg.provider_spec
    if spec.get("provider") == "none":
        return ""
    if spec.get("provider") == "whisper-local" or not spec.get("base_url"):
        local = cfg.transcription.get("local", {})
        return whisper_local(file_path, spec.get("model") or "small", local.get("model_dir", ""))

    base = spec["base_url"].rstrip("/")
    url = base + spec.get("path", "/audio/transcriptions")
    fields = {"model": spec.get("model", "")}
    fields = {k: v for k, v in fields.items() if v}
    body, content_type = _multipart(fields, file_path)
    headers = {"Content-Type": content_type}
    if spec.get("api_key"):
        headers["Authorization"] = f"Bearer {spec['api_key']}"
    req = urlrequest.Request(url, data=body, headers=headers, method="POST")
    with urlrequest.urlopen(req, timeout=120) as reply:
        payload = json.loads(reply.read() or b"{}")
    if spec.get("response_format") == "text":
        return str(payload)
    return str(_dig(payload, spec.get("response_path") or ["text"]))
