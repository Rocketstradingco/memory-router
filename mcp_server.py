#!/usr/bin/env python3
"""Remote HTTP MCP wrapper for memory-router.

Exposes the router's endpoints as MCP tools so any agent adds one URL and calls
them natively. Talks to the plain HTTP service over the docker network.
"""
import os, json, urllib.request
from mcp.server.fastmcp import FastMCP

MR = os.environ.get("MR_BASE", "http://127.0.0.1:8130")
mcp = FastMCP("memory-router",
              host=os.environ.get("MCP_HOST", "0.0.0.0"),
              port=int(os.environ.get("MCP_PORT", "8131")))


def _post(path, payload):
    req = urllib.request.Request(MR + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=35) as r:
        return json.loads(r.read())


@mcp.tool()
def memory_route(fact: str) -> dict:
    """Decide where a fact should be filed before saving it to lab memory.
    Returns a typed decision: bucket (general/windows/kali/parrot/pi), store
    (memory-file/index/repo/dont-store), durable (0-1), sensitive (0-1),
    importance (0-4). If store == 'dont-store', the fact is transient — do NOT save it."""
    return _post("/route", {"state": {"fact": fact}})


@mcp.tool()
def memory_lock_acquire(agent: str, target: str = "CLAUDE.md") -> dict:
    """Acquire a write-lease before editing shared lab memory, so only one agent writes at a time.
    Returns {granted:true, lease, ttl} or {granted:false, wait_seconds, holder}.
    If not granted, wait `wait_seconds` and retry. Always release when done."""
    return _post("/lock/acquire", {"agent": agent, "target": target})


@mcp.tool()
def memory_lock_release(lease: str) -> dict:
    """Release a write-lease after finishing a write to shared lab memory."""
    return _post("/lock/release", {"lease": lease})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
