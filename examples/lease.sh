#!/bin/bash
# Write-lease helper for shell scripts that edit a shared memory file.
#
# Agents use the MCP tools; scripts (cron jobs, rebuild scripts, sync hooks) use
# this. Source it, take a lease, do the work, release -- the trap releases even
# if the script dies halfway.
#
#   source examples/lease.sh
#   lease_acquire CLAUDE.md 300 || exit 1
#   trap lease_release EXIT
#   ... write the shared file ...
#
# Fails OPEN on purpose: if the router is unreachable the script proceeds without
# a lease, because the router coordinates writers, it does not authorise them. It
# refuses only when the target is genuinely held by someone else -- the case
# worth stopping for. Needs curl and python3 (or python).

MEMORY_ROUTER_URL="${MEMORY_ROUTER_URL:-http://127.0.0.1:8130}"
LEASE=""
LEASE_TARGET=""

_lease_py() { command -v python3 >/dev/null 2>&1 && echo python3 || echo python; }

_lease_field() {   # _lease_field <json> <key>
  printf '%s' "$1" | "$(_lease_py)" -c "import sys,json;print(json.load(sys.stdin).get('$2',''))" 2>/dev/null
}

lease_acquire() {   # lease_acquire <target> [ttl_seconds] [attempts]
  local target="$1" ttl="${2:-45}" attempts="${3:-5}" agent i resp wait
  agent="$(hostname)-$$"
  LEASE_TARGET="$target"
  for (( i = 1; i <= attempts; i++ )); do
    resp="$(curl -s --max-time 5 -X POST "$MEMORY_ROUTER_URL/lock/acquire" \
            -d "{\"agent\":\"$agent\",\"target\":\"$target\",\"ttl\":$ttl}" 2>/dev/null)"
    if [ -z "$resp" ]; then
      echo "lease: router unreachable, proceeding without a lease" >&2
      return 0
    fi
    if [ "$(_lease_field "$resp" granted)" = "True" ]; then
      LEASE="$(_lease_field "$resp" lease)"
      echo "lease: holding $target for ${ttl}s" >&2
      return 0
    fi
    wait="$(_lease_field "$resp" wait_seconds)"
    echo "lease: $target held by $(_lease_field "$resp" holder), waiting ${wait}s (try $i/$attempts)" >&2
    sleep "${wait:-5}"
  done
  echo "lease: could not acquire $target after $attempts tries" >&2
  return 1
}

lease_renew() {   # lease_renew [ttl_seconds] -- call periodically during long work
  [ -n "$LEASE" ] || return 0
  curl -s --max-time 5 -X POST "$MEMORY_ROUTER_URL/lock/renew" \
       -d "{\"lease\":\"$LEASE\",\"ttl\":${1:-45}}" >/dev/null 2>&1
}

lease_release() {
  [ -n "$LEASE" ] || return 0
  curl -s --max-time 5 -X POST "$MEMORY_ROUTER_URL/lock/release" \
       -d "{\"lease\":\"$LEASE\"}" >/dev/null 2>&1
  echo "lease: released $LEASE_TARGET" >&2
  LEASE=""
}
