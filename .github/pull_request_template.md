## What this changes

<!-- One or two sentences. One purpose per PR. -->

## Why

<!-- The problem it solves, or link the issue: Fixes #123 -->

## How it was tested

<!-- Paste the relevant output: /health, MCP /health, tools/calibrate.py summary, lease.sh run. -->

## Checklist

- [ ] `app.py` still uses the standard library only
- [ ] No setup-specific names, IPs or paths in code (they belong in `config.json`)
- [ ] Existing endpoints, tool names and response fields still work
- [ ] README updated if behaviour changed
- [ ] No secrets, `.env` or `data/` files included
