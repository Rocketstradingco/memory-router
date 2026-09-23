# Contributing

Contributions are welcome: fixes, features, docs, better example configs. Every change goes through a pull request and is reviewed by the maintainer before it's merged. Nothing lands on `main` without that approval.

## How to submit a change

1. **Fork** this repository and create a branch off `main` (`git checkout -b fix-lease-renew`).
2. **Make the change.** Keep a pull request to one purpose; two unrelated fixes are two PRs.
3. **Test it** (see below).
4. **Open a pull request** against `main` and fill in the template. The maintainer is requested as reviewer automatically.
5. **Respond to review.** Push more commits to the same branch; the PR updates itself. The maintainer merges it once it's approved.

For a larger change (a new endpoint, a new dependency, a change to the config format), open an **issue** first describing what you want to do and why, so nobody spends time on something that won't be merged.

## Ground rules

These keep the project what it is. A PR that breaks one will be asked to change.

- **The core stays dependency-free.** `app.py` uses the Python standard library only. The MCP server may depend on `mcp` (pinned `<2`); nothing else.
- **No setup-specific content in code.** Machine names, IPs, paths and taxonomy belong in `config.json`, never in `app.py` or `mcp_server.py`.
- **The router advises, it never writes.** It must not read or modify anyone's memory files.
- **Backwards compatible API.** Existing endpoints, MCP tool names, parameters and response fields keep working. Add optional parameters and new fields; don't rename or remove.
- **No secrets.** Never commit an API key, a `.env`, or a `data/decisions.jsonl` log. Example values look like `sk-or-your-key-here`.
- **Keep the README true.** If behaviour changes, update the README in the same PR. It's written to be followed step by step by people and AI agents alike, so keep the exact commands and response examples accurate.

## Testing a change

There's no test suite yet (a PR adding one is welcome). Before opening a PR, run through this:

```bash
cp .env.example .env                       # add your own OpenRouter key
docker compose up -d --build
curl -s localhost:8130/health              # ok: true, has_key: true
curl -s localhost:8132/health              # mcp: up, tools: 4 (or more, if you added one)
python3 tools/calibrate.py                 # 0 confidently wrong; exit code 0
source examples/lease.sh && lease_acquire test 60 && lease_renew 90 && lease_release
docker compose down
```

If you changed routing behaviour or `config.json`, include the `calibrate.py` summary lines in the PR description.

## For AI agents preparing a contribution

If you are an AI agent asked to contribute: read `README.md` fully, follow the ground rules above, run the testing steps and report their output in the PR description. Don't widen the change beyond what was asked. State plainly in the PR anything you couldn't test.

## License

By submitting a pull request you agree your contribution is licensed under this project's [MIT License](LICENSE).
