# Routing

Optional. With no `[[routes]]`, nothing is routed: captures land in
`~/.wrist/index.db` and are read through MCP or the CLI.

```toml
[routing]
type = "naive"          # naive | classifier | llm
base_url = ""           # OpenAI-compatible chat endpoint, for classifier/llm
model = ""
api_key_env = ""

[[routes]]
name = "delegate"
about = "a task to hand to an agent"     # read by the llm strategy
match = { any = ["deploy", "remind"] }   # read by the naive strategy
dest = "hermes"

[destinations.hermes]
url = "https://hermes.example"
path_suffix = "/webhooks/telegram-messages"
scheme = "standard-webhooks"
secret_env = "HERMES_WEBHOOK_SECRET"

[destinations.openclaw]
url = "http://gateway.local:18789"
path_suffix = "/hooks/agent"
scheme = "bearer"
token_env = "OPENCLAW_HOOKS_TOKEN"
```

Strategies:
- **naive** - first rule whose `match.any` words appear (case-insensitive). `match.always = true` is a catch-all.
- **classifier** - the model picks one route `name` from the list; falls back to naive on failure.
- **llm** - same call, but the model also reads each route's `about` text.

Failures:
- A route with an unknown `dest` is a `ValueError` at load - a typo must be loud.
- A model that answers a label that does not exist routes nowhere rather than guessing.
