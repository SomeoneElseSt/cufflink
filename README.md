# watchlink

One tap on your Apple Watch, and the audio is on your own machine — then you
decide what happens to it. Nothing is decided for you.

```
watch ──tap──▶ your Mac (or a VM) ──▶ ~/.wrist/recordings/*.m4a + index.db
                                        │
                    ┌───────────────────┼────────────────────┐
                    ▼                   ▼                    ▼
              transcribe            read over MCP       route to an agent
         locally or any service   list_captures / get   webhook (Hermes,
         (OpenAI-compatible)      resources/read        OpenClaw), or nothing
```

## What it gives you

A capture inbox you own, as plain files plus a SQLite index:

- **The audio, as files.** `~/.wrist/recordings/*.m4a`, one per tap, with
  `~/.wrist/index.db` holding id, time, size, transcript, route, status.
  Nothing leaves your machine unless you configure it to.
- **Transcription, optional and pluggable.** Local whisper, or any service that
  speaks the OpenAI audio-transcriptions shape (multipart `file` + `model`,
  Bearer auth, JSON `{"text": ...}`). Providers are data, not code: a chain of
  N names tried in order, first success wins.
- **Reading over MCP.** `list_captures`, `get_capture`, the resource
  `recordings://latest` — any MCP client (Pi, Claude Code, Hermes, OpenClaw,
  your own) can pull the backlog. Nothing needs to be pushed for that.
- **Routing, if you want it.** Deliver each capture to a webhook — Hermes,
  OpenClaw `/hooks/agent`, anything that takes a POST — by keyword match, a
  System-1 classifier, or a reasoning model that picks the destination. With
  no routes, everything goes to the first destination.
- **Optionality is the point.** Use one of these, or all, or none: transcribe
  and read locally; skip transcription and keep raw audio; push to an agent;
  read over MCP from an agent elsewhere. The defaults are quiet, not opinionated.

## Quick start

```
wl setup --json      # asks a few questions, writes ~/.wrist/config.toml
wl serve --detach    # the daemon: POST /upload + watcher + router
wl status --json
wl watch-setup       # builds and imports the Watch shortcut
```

Then tap the shortcut on your watch and start talking. From an agent:

```
captures = list_captures({"limit": 5})   # over MCP, from anywhere
```

## Things people do with it

- **Delegate on the go.** Tap, say "send a sub-agent to work on the front
  end", and have it arrive where your agent is listening.
- **Log medical notes.** "My knee hurts, seven out of ten" — captured, kept
  privately, readable later with its timestamp.
- **Gather context all day.** Record conversations or meetings and keep the
  transcripts together instead of in six apps.
- **Remember something.** Tap, say it, forget it, find it in the index.

## Config

`~/.wrist/config.toml` (0600) is the whole contract — server address, the
transcription chain, routing rules, destinations. `wl config get|set` reads and
writes it; `wl setup` regenerates it from one object, so never append to it by
hand.

Environment: `WATCHLINK_HOME` (data dir, default `~/.wrist`),
`WATCHLINK_INGEST_KEY` (upload auth), `WATCHLINK_MCP_TOKEN` (MCP HTTP auth),
plus provider keys read at call time.

## MCP

```
# stdio, for a local agent
python -m watchlink.mcp_server
# HTTP with a bearer token, for the remote case (watchlink on a VM)
WATCHLINK_MCP_TOKEN=<token> python -m watchlink.mcp_http --http --port 8767
```
