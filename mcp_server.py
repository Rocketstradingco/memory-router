#!/usr/bin/env python3
"""Remote HTTP MCP wrapper for memory-router.

Exposes the router's endpoints as MCP tools so any agent adds one URL and calls
them natively. Talks to the plain HTTP service over the docker network.

Also serves GET /health on MCP_HEALTH_PORT. A plain GET to /mcp returns 406
(streamable-HTTP MCP needs an SSE Accept header), so that port can't be used as
a monitor — the health check here does a real `initialize` handshake instead.
"""
import os, json, threading, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mcp.server.fastmcp import FastMCP

MR = os.environ.get("MR_BASE", "http://127.0.0.1:8130")
PORT = int(os.environ.get("MCP_PORT", "8131"))
HEALTH_PORT = int(os.environ.get("MCP_HEALTH_PORT", "8132"))
PROTOCOL = "2025-06-18"

mcp = FastMCP("memory-router", host=os.environ.get("MCP_HOST", "0.0.0.0"), port=PORT)


def _post(path, payload):
    req = urllib.request.Request(MR + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=35) as r:
        return json.loads(r.read())


TOOLS: list[str] = []


def tool(fn):
    """Register an MCP tool and count it, so /health reports the real number."""
    TOOLS.append(fn.__name__)
    return mcp.tool()(fn)


def _with_ttl(payload: dict, ttl: int | None) -> dict:
    # Omit ttl entirely when not given, so the router applies its own default.
    return payload if ttl is None else {**payload, "ttl": int(ttl)}


@tool
def memory_route(fact: str) -> dict:
    """Decide where a fact should be filed before saving it to lab memory.
    Returns a typed decision: bucket (one of the buckets defined in the router's
    config.json — typically a conventions bucket for rules about how to work, a shared
    bucket for fleet-wide facts, or one specific machine), store
    (memory-file/index/repo/dont-store), durable (0-1), sensitive (0-1),
    importance (0-4). If store == 'dont-store', the fact is transient — do NOT save it."""
    return _post("/route", {"state": {"fact": fact}})


@tool
def memory_lock_acquire(agent: str, target: str = "CLAUDE.md", ttl: int | None = None) -> dict:
    """Acquire a write-lease before editing shared lab memory, so only one agent writes at a time.
    An active lease blocks a new acquire even with the same agent name; use the
    lease token to renew or release it.
    ttl is the lease length in seconds (router default 45, max 600). A lease that runs out
    mid-edit stops protecting you, so for a multi-step edit pass a ttl that covers the whole
    job (e.g. 300), or call memory_lock_renew as you go.
    Returns {granted:true, lease, ttl} or {granted:false, wait_seconds, holder}.
    If not granted, wait `wait_seconds` and retry. Always release when done."""
    return _post("/lock/acquire", _with_ttl({"agent": agent, "target": target}, ttl))


@tool
def memory_lock_renew(lease: str, ttl: int | None = None) -> dict:
    """Extend a write-lease you hold, for work that is taking longer than planned.
    ttl is the new length in seconds from now (router default 45, max 600).
    Returns {renewed:true, lease, ttl} or {renewed:false, error} if it already expired —
    in that case acquire a fresh lease before writing anything else."""
    return _post("/lock/renew", _with_ttl({"lease": lease}, ttl))


@tool
def memory_lock_release(lease: str) -> dict:
    """Release a write-lease after finishing a write to shared lab memory."""
    return _post("/lock/release", {"lease": lease})


def probe_mcp():
    """Do a real MCP initialize against our own endpoint and report what came back."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                       "clientInfo": {"name": "healthcheck", "version": "1"}}}
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/mcp", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=8) as r:
        raw = r.read().decode()
    for line in raw.splitlines():                     # streamable-http replies as SSE
        if line.startswith("data:"):
            raw = line[5:].strip()
            break
    res = json.loads(raw).get("result", {})
    info = res.get("serverInfo", {})
    return {"mcp": "up", "protocol": res.get("protocolVersion"),
            "server": info.get("name"), "version": info.get("version")}


def backend_ok():
    try:
        with urllib.request.urlopen(MR + "/health", timeout=6) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception:
        return False


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] != "/health":
            out, code = {"error": "not found"}, 404
        else:
            try:
                out = {"ok": True, "service": "memory-router-mcp", **probe_mcp(),
                       "tools": len(TOOLS), "router": "up" if backend_ok() else "down"}
                code = 200
            except Exception as e:
                out, code = {"ok": False, "service": "memory-router-mcp", "mcp": "down",
                             "error": str(e)[:200]}, 503
        b = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), Health).serve_forever,
                     daemon=True).start()
    print(f"memory-router-mcp health on :{HEALTH_PORT}/health", flush=True)
    mcp.run(transport="streamable-http")
