# memory-router

A small, self-hosted service that helps several AI agents share one memory without making a mess of it.

When Claude Code, Codex and other agents all read and write the same memory files, two things go wrong:

1. **Where does a new fact go?** Which file or section, and is it even worth keeping, or is it just today's status?
2. **Who may write right now?** Two agents editing the same shared file at once clobber each other or create sync conflicts.

memory-router answers both:

- **Routing.** Send it a fact. It returns a typed decision: which bucket the fact belongs in, whether to store it at all, how durable, sensitive and important it is. The decisions come from [Jev](https://typesafe.ai), a decision model served through OpenRouter that returns structured answers, not prose, so it can only ever pick a bucket you defined.
- **Write-leases.** An agent takes a lease on a target (for example `CLAUDE.md`) before writing and releases it after. Anyone else asking gets told who holds it and how long to wait. Leases expire on their own, so a crashed agent can't block the rest.

**The router advises; the agent does the write.** It never touches your files.

It runs as two small containers: an HTTP API and an MCP server, so agents get it as native tools. The core is Python standard library only, and everything specific to your setup lives in one file, `config.json`.

---

## For AI agents: read this first

If a person has asked you to set up or use memory-router, these are the facts you need:

| You want to… | Do this |
|---|---|
| Install it | Follow [Install](#install). It ends with checks you can run. |
| Describe the user's machines | Edit `config.json` → `routing.questions.bucket.criteria`. See [Configure](#configure-the-taxonomy). |
| Give an agent the tools | Register the MCP endpoint `http://<host>:8131/mcp`. See [Connect an agent](#connect-an-agent). |
| Teach an agent the rules | Paste the [agent protocol](#the-agent-protocol-paste-this-into-claudemd--agentsmd) into its instructions file. |
| Decide where a fact goes | `memory_route(fact)`. Drop it if `store` is `dont-store`. |
| Edit a shared file safely | `memory_lock_acquire` → write → `memory_lock_release`. Pass a `ttl` that covers the whole edit. |
| Lock from a shell script | `source examples/lease.sh`. See [Scripts](#scripts). |
| Check the config works | `python3 tools/calibrate.py`. See [Check your taxonomy](#check-your-taxonomy). |

Rules that always hold:

- The router **never writes files**. You do the write it suggests.
- A lease **expires after its ttl** (default 45 seconds, maximum 600). An expired lease protects nothing. Take a ttl long enough for the whole edit, or renew as you go.
- The lease token proves ownership. An active lease blocks another acquire even when the caller uses the same agent name.
- Always **release** a lease when you finish, even if the write failed.
- `/route` sends the fact text to a **cloud API** (OpenRouter). Never route a secret value; route a description of it ("the API key is in .env").
- There is **no authentication**. Anyone who can reach the ports can use it.

---

## Install

Requirements: Docker with the compose plugin, and an [OpenRouter](https://openrouter.ai) API key.

```bash
git clone https://github.com/Rocketstradingco/memory-router
cd memory-router
cp .env.example .env
# edit .env: set OPENROUTER_API_KEY=sk-or-...
# edit config.json: replace the example machines (see Configure)
docker compose up -d --build
```

Check it, in this order:

```bash
curl -s localhost:8130/health
# -> {"ok": true, "service": "memory-router", "has_key": true, "model": "typesafe/jev-1.13", "locks_held": 0}
#    has_key must be true, or every /route call will fail.

curl -s localhost:8132/health
# -> {"ok": true, "service": "memory-router-mcp", "mcp": "up", ..., "tools": 4, "router": "up"}

curl -s -X POST localhost:8130/route -d '{"text":"Never run a destructive git command without asking first."}'
# -> {"suggestion": {"bucket": "conventions", "store": "memory-file", ...}, "answers": {...}, "usage": {...}}
```

**Reaching it from other machines.** By default the ports are published on `127.0.0.1` only. To let agents on other machines use it, set `BIND_ADDR` in `.env` to this machine's LAN IP and run `docker compose up -d`. Only do this on a network you trust. The host-side ports can also be changed in `.env` (`ROUTER_PORT`, `MCP_PORT`, `MCP_HEALTH_PORT`).

---

## Configure the taxonomy

Everything setup-specific is in `config.json`. The code contains none of it.

```text
config.json
├── jev.model / jev.endpoint           which decision model to call (leave as-is)
├── lock.ttl_seconds                   default lease length (45)
├── lock.ttl_max_seconds               longest lease a caller may ask for (600)
└── routing.questions                  sent to the model VERBATIM on every /route call
    ├── bucket     (choice)            WHERE the fact belongs  <- edit this one
    ├── store      (choice)            memory-file | index | repo | dont-store
    ├── durable    (0–1)               still true and useful in weeks?
    ├── sensitive  (0–1)               contains a secret or points at one?
    └── importance (0–4 score)         trivial … critical
```

**The buckets.** The example ships with five:

| Bucket | Meaning | Keep? |
|---|---|---|
| `conventions` | Rules about how the assistant should work: tone, what to ask first, what never to do. | Keep |
| `shared` | Facts equally true of every machine: network map, shared keys, architecture. | Keep |
| `workstation`, `server`, `edge` | Example machines. | Replace with your own, as many as you have |

How to write a machine's criteria, which is what makes routing accurate:

1. **Name what only that machine has:** its distinctive services, ports, paths and hardware ("runs Postgres and the backup container, /srv paths, port 5432"). The model recognises a machine by its belongings, even when a fact never names the machine.
2. **Leave out what every machine has.** Docker, SSH or systemd on every box identifies nothing and only adds noise.
3. **Behaviour rules beat machines.** "Leave the server's backup notes alone during cleanup" is a `conventions` rule even though it names the server. The shipped instructions already say this; keep that sentence if you rewrite them.
4. **Usage notes stay with their machine.** "The SDK lives here; always build with flag X" describes a machine, even though it's phrased as an instruction.

Everything under `routing.questions` is sent to the model verbatim, so keep your own notes in `routing._comment`, never inside a question. After editing:

```bash
docker compose restart memory-router      # config is mounted, no rebuild needed
python3 tools/calibrate.py                # then check the result
```

Restarting drops any leases held at that moment, because leases live in memory.

---

## Connect an agent

The MCP endpoint is streamable HTTP at `http://<host>:8131/mcp`.

**Claude Code:**
```bash
claude mcp add --transport http memory-router http://<host>:8131/mcp --scope user
```

**Codex CLI:**
```bash
codex mcp add memory-router --url http://<host>:8131/mcp
```

**Any MCP client that reads a JSON config** (for example a project `.mcp.json`):
```json
{ "mcpServers": { "memory-router": { "type": "http", "url": "http://<host>:8131/mcp" } } }
```

A running agent sees new or changed tools only after it restarts or reconnects.

---

## The agent protocol (paste this into CLAUDE.md / AGENTS.md)

```markdown
## Shared memory: use memory-router

Before saving a fact to shared memory or editing a shared memory file:

1. Call `memory_route(fact)` with a one- or two-sentence description of the fact.
   - If `store` is `dont-store`, the fact is transient: do not save it.
   - Otherwise save it under the returned `bucket`. If `sensitive` is high,
     record where the secret lives, never the secret itself.
2. Call `memory_lock_acquire(agent="<your name>", target="<file you will edit>", ttl=<seconds>)`.
   - Pick a ttl that covers the whole edit (e.g. 300; max 600).
   - If `granted` is false, wait `wait_seconds`, then try again.
3. Make the edit. If it is taking longer than planned, call `memory_lock_renew(lease, ttl)`
   before the lease runs out. If renew says the lease expired, acquire a new one
   before writing anything else.
4. Call `memory_lock_release(lease)` when done, even if the edit failed.

If the tools are unavailable, write carefully and keep edits small; do not block on the router.
```

---

## MCP tools reference

| Tool | Parameters | Returns |
|---|---|---|
| `memory_route` | `fact: str` | `{"suggestion": {bucket, store, durable, sensitive, importance}, "answers": {...}, "usage": {...}}` |
| `memory_lock_acquire` | `agent: str`, `target: str = "CLAUDE.md"`, `ttl: int \| null = null` | `{"granted": true, "lease", "ttl", "target"}` or `{"granted": false, "wait_seconds", "holder"}` |
| `memory_lock_renew` | `lease: str`, `ttl: int \| null = null` | `{"renewed": true, "lease", "ttl", "target"}` or `{"renewed": false, "error"}` |
| `memory_lock_release` | `lease: str` | `{"released": true, "target"}` or `{"released": false, "error"}` |

Details:

- `target` is any string naming the shared resource. It doesn't have to be a file name, but using the file's name keeps it obvious.
- `ttl` is in seconds. Leaving it out gives `lock.ttl_seconds` (45). Anything above `lock.ttl_max_seconds` (600) is capped, and the reply's `ttl` shows what you actually got.
- A lease can only be renewed or released by whoever holds its `lease` token. There is no "force unlock": an abandoned lease simply expires.
- Calling acquire again with the same agent name does not replace an active lease. Keep its token and call renew if you need more time.
- `answers` holds the model's full answer for each question, including a `confidence` (0–1) for each choice. A low-confidence bucket (below about 0.7) is a guess; consider keeping the previous placement.

Run `python3 -m unittest -v test_lease.py` to check lease ownership without calling Jev.

---

## HTTP API reference (port 8130)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{ok, service, has_key, model, locks_held}` |
| POST | `/route` | `{"text": "..."}` or `{"state": {"fact": "..."}}` | `{suggestion, answers, usage}` |
| POST | `/lock/acquire` | `{"agent": "a", "target": "CLAUDE.md", "ttl": 300}` | `{granted, lease, ttl, target}` or `{granted: false, wait_seconds, holder}` |
| POST | `/lock/renew` | `{"lease": "...", "ttl": 300}` | `{renewed, lease, ttl, target}` or `{renewed: false, error}` |
| POST | `/lock/release` | `{"lease": "..."}` | `{released, target}` or `{released: false, error}` |
| GET | `/decisions?limit=N` | — | recent routing decisions from the audit log, plus call/cost totals |
| GET | `/usage` | — | OpenRouter credit and spend, plus local routing stats (cached 30 s) |

Errors: `400` bad JSON, `404` unknown path, `502` the model backend returned an error (with its detail), `500` anything else.

**Gotcha:** a top-level `{"fact": "..."}` is **ignored**. The router then classifies an empty string, which comes back as a confident-looking but meaningless answer. Use `{"text": ...}` or `{"state": {"fact": ...}}`. The MCP tool does this for you.

Every `/route` call is appended to `data/decisions.jsonl` (time, first 300 characters of the fact, suggestion, average confidence, cost), so you can audit and tune the routing over time.

---

## Scripts

Agents use the MCP tools. Shell scripts that edit shared memory (cron jobs, rebuild scripts, sync hooks) can use `examples/lease.sh`:

```bash
source examples/lease.sh                 # MEMORY_ROUTER_URL defaults to http://127.0.0.1:8130
lease_acquire CLAUDE.md 300 || exit 1    # waits and retries if someone else holds it
trap lease_release EXIT                  # released even if the script dies
# ... write the shared file ...
```

It fails open: if the router can't be reached, the script proceeds without a lease rather than leave your files stale. It stops only when someone else really holds the target.

---

## Check your taxonomy

`tools/calibrate.py` sends a set of labelled facts through `/route` and reports two things: whether the answers were right, and whether the model's confidence was honest.

```bash
MEMORY_ROUTER_URL=http://127.0.0.1:8130 python3 tools/calibrate.py
```

The number that matters is **confidently wrong**: wrong answers given with 0.9+ confidence. No threshold can catch those. Replace the example `CASES` in the script with a dozen facts from your own setup, including a few behaviour rules that name a machine and a few transient ones that shouldn't be stored. Re-run it after every `config.json` change. It exits with code 1 if anything came back confidently wrong.

---

## Operations and troubleshooting

| Symptom | Cause / fix |
|---|---|
| `GET :8131/mcp` returns **406** | Normal: streamable-HTTP MCP requires an SSE `Accept` header. For monitoring, use `GET :8132/health`, which does a real MCP handshake. |
| A code change has no effect | `app.py` and `mcp_server.py` are built into the images. Run `docker compose up -d --build`; a plain `restart` keeps the old code. |
| A `config.json` change has no effect | Config is mounted, so run `docker compose restart memory-router`. That drops held leases. |
| Every `/route` fails | Check `has_key` in `/health`, and your OpenRouter credit at `GET /usage`. |
| Wrong buckets | Make each machine's criteria more distinctive, then run `tools/calibrate.py`. See [Configure](#configure-the-taxonomy). |
| Agents don't see a new tool | MCP clients cache tool lists. Restart or reconnect the agent. |

Leases live in the router's memory, so run exactly one router for any set of agents that share files.

---

## Security

- **No authentication.** Publish the ports on `127.0.0.1` (the default) or a network you trust. Don't expose them to the internet.
- **Fact text goes to OpenRouter.** Route descriptions of facts, never secret values. The `sensitive` score flags text that looks like it contains or points at a secret.
- `/usage` shows your OpenRouter spend and credit, but no account identifiers.
- `.env` (your key) and `data/` (the decision log) are git-ignored.

## Cost

Jev is billed through OpenRouter at about $0.04 per million input tokens, and output is free. A routing call costs a few hundred-thousandths of a dollar, and lock calls cost nothing. `GET /usage` shows live spend.

## Contributing

Pull requests are welcome. Fork, branch, and open a PR against `main`; the maintainer reviews and approves every change before it's merged. See [CONTRIBUTING.md](CONTRIBUTING.md) for the ground rules and the checks to run first.

## License

MIT, see [LICENSE](LICENSE).
