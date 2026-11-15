# watchlink

> Hello, human!
>
> If you're reading this, feel free to copy the entire page and give it to your agent.
>
> It will walk through setting up: 1) your computer or a virtual machine to save your recordings/transcripts on, 2) a transcription service of your choosing to transcribe the recordings, 3) a MCP server to query said transcripts, and optionally 4) wire that up to a personal agent like Hermes or OpenClaw to delegate things easily.
>
> During set up, you must be on a Mac so the agent can add the shortcut to let your Apple Watch send recordings.
>
> It will add it and you should then open the shortcuts app on your watch and wait until it's synced.
>
> Once synced, when you run it for the first time it will ask you for microphone permissions and for permissions to send your data.
>
> Permit always allow for both.
>
> You can then add the shortcut in the Watch's control center or as a shortcut on an Apple Watch ultra, letting you easily start a recording.


One tap on your Apple Watch, and the audio is on your own machine. From there
you can transcribe it, read it over MCP, or route it to an agent.

```
watch ──tap──▶ your Mac (or a VM) ──▶ ~/.wrist/recordings/*.m4a + index.db
                                        │
                    ┌───────────────────┼────────────────────┐
                    ▼                   ▼                    ▼
              transcribe            read over MCP       route to an agent
         locally or any service   list_captures / get   webhook (Hermes,
         (OpenAI-compatible)      resources/read        OpenClaw), or nothing
                                                              │
                                          optional, with many agents or
                                          loggers: classify first, then
                                          pick - keyword, System-1
                                          classifier, or a reasoning model
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
- **Use any of it, or none.** Transcribe and read locally, keep raw audio,
  push to an agent, or read over MCP from an agent elsewhere.

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

## Troubleshooting

- **A capture came in but no reply anywhere.** Check `wl status --json`: if a
  destination is configured but the delivery secret is missing from the
  daemon's environment, the delivery fails with 401 and the row stays
  `delivery_failed` in the index. Restart the daemon with the secret set.
- **The watch tap does nothing.** The shortcut must be imported where the
  watch syncs from (the iPhone), and the first run asks for microphone and
  network permission — allow both. Also check the ingest key baked into the
  shortcut is the *daemon's* key (a Mac-built shortcut against a VM ingest
  gets a silent 401).
- **`recordings` is empty after a day.** With the daemon on your Mac, uploads
  only work while it is awake; a closed lid means the recording goes nowhere.
  Run it on a VM if you want it always reachable.
- **Config errors like `Cannot declare ('transcription', 'adapters', ...) twice`.**
  Never append tables to `config.toml` by hand — regenerate it with
  `wl setup`.
