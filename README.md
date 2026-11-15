# cufflog

Talk into your wrist, get it done on your machine.

Record on an Apple Watch with one tap, the audio lands on your Mac (or your own
server), gets transcribed however you like, and is routed to whatever agent you
use — Hermes, OpenClaw, Claude Code — as a message it can act on.

- **Self-hosted by default.** Your recordings are files on your disk
  (`~/.wrist/recordings/`), the index is SQLite, nothing leaves your machine
  unless you configure a destination.
- **Pluggable transcription.** Local whisper, or any service speaking the
  OpenAI audio-transcriptions shape (multipart `file` + `model`, Bearer auth,
  JSON `{"text": ...}`). Not configured? You get a placeholder file telling you
  (or your agent) exactly what to run.
- **Agent-first.** Every command is non-interactive and speaks `--json`; MCP
  exposes the captures for reading, webhooks push them out.

## Quick start

```
wc setup --json      # asks a few questions, writes ~/.wrist/config.toml
wc serve             # the daemon: POST /upload + watcher + router
wc status --json
```

Then set up the watch shortcut (`wc watch-setup`) and start talking.
