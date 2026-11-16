# watchlink-receiver (Hermes plugin)

Puts a wrist capture into your own Hermes conversation as a user turn.

```
Watch -> watchlink transcribes -> watchlink route -> this plugin -> this chat
```

## Why a plugin

Hermes's `inject_message` has no public HTTP endpoint: the plugin must already
know the target chat. This one targets it by `origin` (`platform` + `chat_id`),
which resolves to the existing session, so the turn is answered with your
conversation's context.

## Config

`config.yaml`:

```yaml
plugins:
  entries:
    watchlink-receiver:
      allow_gateway_injection: true
      settings:
        platform: telegram
        chat_id: "6574561645"
        host: 127.0.0.1
        port: 8770
        token_env: WATCHLINK_RECEIVER_TOKEN
```

`allow_gateway_injection: true` is required - Hermes refuses gateway injection
by default for every plugin. Set `WATCHLINK_RECEIVER_TOKEN` in the gateway's
environment to require a bearer token on the receiver; leave it unset for an
open local listener.

## watchlink side

The receiver speaks the same JSON the daemon delivers:

```toml
[[routes]]
name = "into-my-chat"
about = "a note for the conversation I am having with my agent"
dest = "hermes"

[destinations.hermes]
url = "http://127.0.0.1:8770"
path_suffix = "/capture"
scheme = "bearer"
token_env = "WATCHLINK_RECEIVER_TOKEN"
```

## Safety

A transcript is not necessarily an instruction from you: the watch mic picks up
whoever is talking. Each injected turn starts with
`[watchlink capture <id> - recorded from the wrist microphone, may include other speakers]`,
and Hermes treats injected text as conversational input only - it can never run a
slash command, approve a tool, or answer a pending prompt.
