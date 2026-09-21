# memory-router

A tiny, self-hostable **routing + write-lease service for multi-agent memory systems.**

When several AI agents (Claude Code, Codex, your own) share one memory file or repo, two problems show up:

1. **Where does a new fact go?** Which bucket / file / repo — and is it even worth keeping, or is it just transient status?
2. **Who's allowed to write right now?** Two agents editing the same shared file at once produces sync conflicts and clobbered edits.

`memory-router` answers both with one small service:

- **`/route`** — send it a fact; it returns a **typed decision** (bucket, store, durable, sensitive, importance) from [**Jev**](https://typesafe.ai), TypeSafe AI's System One model — a decision model that returns *structured typed answers instead of text*, so it's cheap, fast, and can't hallucinate a bucket that doesn't exist. **The router advises; your agent does the actual write.**
- **`/lock`** — a write-lease so only one agent edits a given target at a time (with a TTL so a crashed agent can't deadlock the rest).

It ships with an **MCP server**, so agents call `memory_route` / `memory_lock_acquire` / `memory_lock_release` as native tools.

> Dependency-free core (Python stdlib), config-driven, no vendor lock beyond the decision backend — swap the model/endpoint in `config.json`.

## Quickstart

```bash
git clone https://github.com/Rocketstradingco/memory-router
cd memory-router
cp .env.example .env            # then put your OpenRouter key in .env
# edit config.json -> rename the "bucket" options to your own machines/scopes
docker compose up -d --build
curl localhost:8130/health
```

You need an [OpenRouter](https://openrouter.ai) key; Jev is served at `typesafe/jev-1.13` via OpenRouter's alpha *Decisions* endpoint (input ≈ $0.042/M tokens, output free — a routing call costs a few hundred-thousandths of a cent).

## Make it yours

Everything setup-specific lives in **`config.json`** — the code has none of it.
- `routing.questions.bucket.criteria` — your machines/scopes.
- `routing.questions.store` — where things can be filed.
- `durable` / `sensitive` / `importance` — tune the instructions to your taste.
- `jev.model` / `jev.endpoint` — point at a different backend if you like.

Config is volume-mounted, so after editing: `docker compose restart memory-router`.

## HTTP API (port 8130)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | status |
| POST | `/route` | `{"state":{...}}` or `{"text":"..."}` | `{suggestion, answers, usage}` |
| POST | `/lock/acquire` | `{"agent":"a","target":"CLAUDE.md"}` | `{granted,lease,ttl}` or `{granted:false,wait_seconds,holder}` |
| POST | `/lock/release` | `{"lease":"..."}` | `{released}` |

`suggestion` example: `{"bucket":"server","store":"memory-file","durable":0.81,"sensitive":0.02,"importance":2.4}`. `store == "dont-store"` means it's transient — don't save it.

## MCP (port 8131)

A streamable-HTTP MCP server exposes the same actions as tools: `memory_route`, `memory_lock_acquire`, `memory_lock_release`.

Add it to Claude Code:
```bash
claude mcp add --transport http memory-router http://<host>:8131/mcp --scope user
```

## Agent flow

1. `memory_route(fact)` → if `store == "dont-store"`, drop it; else note the bucket/store.
2. `memory_lock_acquire(agent, target)` → if `wait_seconds`, wait and retry.
3. Write the fact where the route said.
4. `memory_lock_release(lease)`.

## Notes & caveats

- **The decision backend (Jev) is a cloud API** — routing calls leave your network. Don't send secrets to `/route`; it's meant to route *metadata about* facts, not the sensitive values themselves.
- **No fallback in this version** — if the backend is unreachable, `/route` returns an error. Add your own rule-based fallback if you need offline operation.
- The lock is a single-instance in-memory mutex (run one router instance per shared target).

## License

MIT — see [LICENSE](LICENSE).
