#!/usr/bin/env python3
"""memory-router v1 — a Jev-backed routing + write-lease service.

Generic by design: all taxonomy/rules live in config.json (not in code),
the API key comes from the OPENROUTER_API_KEY env var. Dependency-free (stdlib).

Endpoints:
  GET  /health
  POST /route          {"state": {...}} or {"text": "..."}  -> typed routing decision (Jev)
  POST /lock/acquire   {"agent": "...", "target": "CLAUDE.md"} -> {granted, lease, ttl} | {wait_seconds}
  POST /lock/release   {"lease": "..."} -> {released}
"""
import json, os, time, uuid, threading, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = json.load(open(os.environ.get("ROUTER_CONFIG", "/app/config.json"), encoding="utf-8"))
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
JEV = CONFIG["jev"]
LOCK_TTL = int(CONFIG.get("lock", {}).get("ttl_seconds", 45))

_locks = {}                      # target -> {lease, agent, expires}
_mu = threading.Lock()


def jev_decide(state):
    body = {"model": JEV["model"], "state": state, "questions": CONFIG["routing"]["questions"]}
    req = urllib.request.Request(
        JEV["endpoint"], data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def acquire(target, agent):
    now = time.time()
    with _mu:
        cur = _locks.get(target)
        if cur and cur["expires"] > now and cur["agent"] != agent:
            return {"granted": False, "wait_seconds": round(cur["expires"] - now, 1), "holder": cur["agent"]}
        lease = uuid.uuid4().hex
        _locks[target] = {"lease": lease, "agent": agent, "expires": now + LOCK_TTL}
        return {"granted": True, "lease": lease, "ttl": LOCK_TTL, "target": target}


def release(lease):
    with _mu:
        for t, info in list(_locks.items()):
            if info["lease"] == lease:
                del _locks[t]
                return {"released": True, "target": t}
    return {"released": False, "error": "unknown or expired lease"}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "service": "memory-router", "has_key": bool(API_KEY),
                                    "model": JEV["model"], "locks_held": len(_locks)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            data = self._body()
        except Exception as e:
            return self._send(400, {"error": f"bad json: {e}"})
        try:
            if self.path == "/route":
                state = data.get("state") or {"text": data.get("text", "")}
                res = jev_decide(state)
                ans = res.get("answers", {})
                flat = {k: (v.get("choice") if "choice" in v else v.get("noul") if "noul" in v else v.get("score"))
                        for k, v in ans.items()}
                return self._send(200, {"suggestion": flat, "answers": ans, "usage": res.get("usage")})
            if self.path == "/lock/acquire":
                return self._send(200, acquire(data.get("target", "CLAUDE.md"), data.get("agent", "unknown")))
            if self.path == "/lock/release":
                return self._send(200, release(data.get("lease", "")))
            self._send(404, {"error": "not found"})
        except urllib.error.HTTPError as e:
            self._send(502, {"error": "jev http error", "code": e.code, "detail": e.read().decode()[:600]})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8130"))
    print(f"memory-router listening on :{port} (model={JEV['model']}, lock_ttl={LOCK_TTL}s, key={'set' if API_KEY else 'MISSING'})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
