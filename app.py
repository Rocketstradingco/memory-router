#!/usr/bin/env python3
"""memory-router v1 — a Jev-backed routing + write-lease service.

Generic by design: all taxonomy/rules live in config.json (not in code),
the API key comes from the OPENROUTER_API_KEY env var. Dependency-free (stdlib).

Endpoints:
  GET  /health
  GET  /usage          -> live OpenRouter credit/spend + local routing stats (cached)
  GET  /decisions      -> recent routing decisions (?limit=N)
  POST /route          {"state": {...}} or {"text": "..."}  -> typed routing decision (Jev)
  POST /lock/acquire   {"agent": "...", "target": "CLAUDE.md", "ttl": 300} -> {granted, lease, ttl} | {wait_seconds, holder}
  POST /lock/renew     {"lease": "...", "ttl": 300} -> {renewed, ttl} | {renewed: false, error}
  POST /lock/release   {"lease": "..."} -> {released}
  (ttl is optional: default lock.ttl_seconds, capped at lock.ttl_max_seconds)
"""
import json, os, time, uuid, threading, urllib.request, urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = json.load(open(os.environ.get("ROUTER_CONFIG", "/app/config.json"), encoding="utf-8"))
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
JEV = CONFIG["jev"]
LOCK_TTL = int(CONFIG.get("lock", {}).get("ttl_seconds", 45))
# Ceiling for a caller-specified TTL. Long enough for a full vault rebuild,
# short enough that a crashed writer frees the target without help.
LOCK_TTL_MAX = int(CONFIG.get("lock", {}).get("ttl_max_seconds", 600))
DATA = os.environ.get("ROUTER_DATA", "/app/data")
LOG = os.path.join(DATA, "decisions.jsonl")
OR_API = "https://openrouter.ai/api/v1"
USAGE_TTL = int(os.environ.get("USAGE_CACHE_SECONDS", "30"))

_locks = {}                      # target -> {lease, agent, expires}
_mu = threading.Lock()
_logmu = threading.Lock()
_usage_cache = {"at": 0, "data": None}


def jev_decide(state):
    body = {"model": JEV["model"], "state": state, "questions": CONFIG["routing"]["questions"]}
    req = urllib.request.Request(
        JEV["endpoint"], data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def log_decision(state, flat, answers, usage):
    """Append one decision so routing quality can be audited/tuned over time."""
    conf = [v.get("confidence") for v in answers.values() if isinstance(v, dict) and "confidence" in v]
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "fact": str(state.get("fact") or state.get("text") or state)[:300],
           "suggestion": flat,
           "confidence_avg": round(sum(conf) / len(conf), 3) if conf else None,
           "cost": (usage or {}).get("cost")}
    try:
        os.makedirs(DATA, exist_ok=True)
        with _logmu, open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass                      # logging must never break a routing call
    return rec


def read_decisions(limit=None):
    try:
        with open(LOG, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return rows[-limit:] if limit else rows


def router_stats():
    rows = read_decisions()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    td = [r for r in rows if str(r.get("ts", "")).startswith(today)]
    cost = lambda rs: round(sum(r.get("cost") or 0 for r in rs), 6)
    confs = [r["confidence_avg"] for r in rows if r.get("confidence_avg") is not None]
    return {"router_calls": len(rows), "router_calls_today": len(td),
            "router_cost": cost(rows), "router_cost_today": cost(td),
            "router_confidence_avg": round(sum(confs) / len(confs), 3) if confs else None}


def _or_get(path):
    req = urllib.request.Request(OR_API + path, headers={"Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("data", {})


def usage(force=False):
    """Live OpenRouter spend + local routing stats. Cached so the dashboard can poll fast.

    Account identifiers (key label, user/org/workspace ids) are deliberately not
    exposed — this is served unauthenticated on the LAN.
    """
    now = time.time()
    if not force and _usage_cache["data"] and now - _usage_cache["at"] < USAGE_TTL:
        out = dict(_usage_cache["data"])
        out["cache_age"] = int(now - _usage_cache["at"])
        out.update(router_stats())          # local stats are cheap — never serve them stale
        out["locks_held"] = len(_locks)
        out.update(_fmt_money(out))
        return out

    out = {"ok": True, "cache_age": 0}
    try:
        c = _or_get("/credits")
        total, used = float(c.get("total_credits") or 0), float(c.get("total_usage") or 0)
        out.update(credits_total=round(total, 6), credits_used=round(used, 6),
                   credits_remaining=round(total - used, 6),
                   credits_pct_remaining=round((total - used) / total * 100, 2) if total else None)
    except Exception as e:
        out.update(ok=False, credits_error=str(e)[:200])
    try:
        k = _or_get("/key")
        free = k.get("free_model_daily_requests") or {}
        out.update(key_limit=k.get("limit"), key_used=round(float(k.get("usage") or 0), 6),
                   key_remaining=round(float(k.get("limit_remaining") or 0), 6),
                   spend_today=round(float(k.get("usage_daily") or 0), 6),
                   spend_week=round(float(k.get("usage_weekly") or 0), 6),
                   spend_month=round(float(k.get("usage_monthly") or 0), 6),
                   free_requests_remaining=free.get("remaining"))
    except Exception as e:
        out.update(ok=False, key_error=str(e)[:200])

    out.update(router_stats())
    out["locks_held"] = len(_locks)
    out.update(_fmt_money(out))
    _usage_cache.update(at=now, data=out)
    return dict(out)


def money(v):
    """Dashboard-ready string. Routing costs are ~$0.00004, which a 2dp
    currency formatter would render as $0.00 — so scale the precision."""
    if v is None:
        return None
    return f"${v:,.2f}" if v >= 1 else f"${v:.4f}" if v >= 0.01 else f"${v:.6f}"


def _fmt_money(d):
    keys = ("credits_remaining", "credits_used", "spend_today", "spend_week",
            "spend_month", "key_remaining", "router_cost", "router_cost_today")
    return {f"{k}_fmt": money(d.get(k)) for k in keys if d.get(k) is not None}


def _clamp_ttl(ttl):
    """A caller may ask for longer, but not forever."""
    try:
        ttl = int(ttl)
    except (TypeError, ValueError):
        return LOCK_TTL
    return max(1, min(ttl, LOCK_TTL_MAX))


def acquire(target, agent, ttl=None):
    now = time.time()
    seconds = _clamp_ttl(ttl) if ttl is not None else LOCK_TTL
    with _mu:
        cur = _locks.get(target)
        if cur and cur["expires"] > now and cur["agent"] != agent:
            return {"granted": False, "wait_seconds": round(cur["expires"] - now, 1), "holder": cur["agent"]}
        lease = uuid.uuid4().hex
        _locks[target] = {"lease": lease, "agent": agent, "expires": now + seconds}
        return {"granted": True, "lease": lease, "ttl": seconds, "target": target}


def renew(lease, ttl=None):
    """Extend a lease the caller already holds.

    For work whose duration is not known up front: heartbeat rather than
    guessing a TTL. Only the holder can renew, since the lease is the proof.
    """
    now = time.time()
    seconds = _clamp_ttl(ttl) if ttl is not None else LOCK_TTL
    with _mu:
        for t, info in _locks.items():
            if info["lease"] == lease:
                if info["expires"] <= now:
                    return {"renewed": False, "error": "lease already expired"}
                info["expires"] = now + seconds
                return {"renewed": True, "lease": lease, "ttl": seconds, "target": t}
    return {"renewed": False, "error": "unknown or expired lease"}


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
        path = self.path.split("?")[0]
        if path == "/health":
            return self._send(200, {"ok": True, "service": "memory-router", "has_key": bool(API_KEY),
                                    "model": JEV["model"], "locks_held": len(_locks)})
        if path == "/usage":
            try:
                return self._send(200, usage())
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
        if path == "/decisions":
            n = 20
            if "limit=" in self.path:
                try:
                    n = max(1, min(500, int(self.path.split("limit=")[1].split("&")[0])))
                except ValueError:
                    pass
            return self._send(200, {"decisions": read_decisions(n), **router_stats()})
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
                log_decision(state, flat, ans, res.get("usage"))
                return self._send(200, {"suggestion": flat, "answers": ans, "usage": res.get("usage")})
            if self.path == "/lock/acquire":
                return self._send(200, acquire(data.get("target", "CLAUDE.md"),
                                               data.get("agent", "unknown"),
                                               data.get("ttl")))
            if self.path == "/lock/renew":
                return self._send(200, renew(data.get("lease", ""), data.get("ttl")))
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
