#!/usr/bin/env python3
"""Check that your config.json routes facts the way you intend.

Two questions, and they are different:

  1. Is it right?             -- accuracy against labelled cases.
  2. Does it know when it     -- a model that is 60% confident and 60% right is
     is unsure?                  useful; one that is 99% confident and 60% right
                                 is dangerous, because no confidence threshold
                                 can catch its mistakes.

The CASES below match the example config. Replace them with facts from your own
setup (a dozen is plenty) and re-run after every config.json change.

  MEMORY_ROUTER_URL=http://127.0.0.1:8130 python3 tools/calibrate.py

Exit code is 1 if anything came back confidently wrong, so it can gate a CI job.
Stdlib only. Each case is one routing call (a few hundred-thousandths of a dollar).
"""

import json
import os
import sys
import urllib.request

ROUTER = os.environ.get("MEMORY_ROUTER_URL", "http://127.0.0.1:8130").rstrip("/") + "/route"
CONFIDENT = 0.90     # wrong at or above this = the dangerous kind of error
UNSURE = 0.70        # right below this = a threshold would throw away a good answer

# (text, expected_bucket, expected_store) -- None means "no single right answer"
CASES = [
    # --- facts about one machine ---
    ("The desktop's GPU has 8 GB of VRAM, so local models top out around 7B at Q4.",
     "workstation", "memory-file"),
    ("Postgres and the nightly backup container run in Docker on the home server; "
     "backups land in /srv/backups.", "server", "memory-file"),
    ("The Raspberry Pi reads the temperature sensor on GPIO 4 and posts it every minute.",
     "edge", "memory-file"),

    # --- behaviour rules, including ones that name a machine ---
    ("Never run a destructive git command without asking first.", "conventions", "memory-file"),
    ("State a risk once; if the user says they understand, stop repeating it.",
     "conventions", "memory-file"),
    ("During memory cleanup, leave the server's backup notes alone unless asked.",
     "conventions", "memory-file"),

    # --- fleet-wide facts ---
    ("All machines are on 192.168.1.0/24 and share one SSH key for admin access.",
     "shared", "memory-file"),

    # --- transient: must NOT be stored ---
    ("The build failed just now because of a typo; fixed it and it passes.", None, "dont-store"),
    ("The server is rebooting right now.", None, "dont-store"),

    # --- genuinely ambiguous: low confidence is the CORRECT behaviour ---
    ("Docker is installed on both the desktop and the server.", None, None),
]


def ask(text: str) -> dict:
    req = urllib.request.Request(ROUTER, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def main() -> int:
    hits = total = store_hits = store_total = 0
    confident_wrong, unsure_right, rows = [], [], []

    for text, want_bucket, want_store in CASES:
        try:
            data = ask(text)
        except Exception as exc:  # noqa: BLE001
            print(f"router error: {exc}", file=sys.stderr)
            return 2
        sug, ans = data.get("suggestion", {}), data.get("answers", {})
        got, got_store = sug.get("bucket"), sug.get("store")
        conf = ans.get("bucket", {}).get("confidence", 0.0)
        conf_store = ans.get("store", {}).get("confidence", 0.0)

        mark = ".. "
        if want_bucket is not None:
            total += 1
            ok = got == want_bucket
            hits += ok
            mark = "ok " if ok else "XX "
            if not ok and conf >= CONFIDENT:
                confident_wrong.append((text[:60], want_bucket, got, conf))
            if ok and conf < UNSURE:
                unsure_right.append((text[:60], got, conf))
        if want_store is not None:
            store_total += 1
            store_hits += got_store == want_store
        rows.append((mark, text[:56], f"{got}@{conf:.2f}", f"{got_store}@{conf_store:.2f}",
                     want_bucket or "-", want_store or "-"))

    print(f"   {'case':<56} {'got bucket':<18} {'got store':<20} {'want':<12} want store")
    for r in rows:
        print(f"{r[0]}{r[1]:<56} {r[2]:<18} {r[3]:<20} {r[4]:<12} {r[5]}")
    print()
    print(f"bucket accuracy : {hits}/{total}")
    print(f"store accuracy  : {store_hits}/{store_total}")
    print(f"confidently wrong (>= {CONFIDENT} and incorrect): {len(confident_wrong)}")
    for t, want, got, c in confident_wrong:
        print(f"    {t}... wanted {want}, got {got} @ {c:.2f}")
    print(f"right but unsure (< {UNSURE} and correct): {len(unsure_right)}")
    for t, got, c in unsure_right:
        print(f"    {t}... {got} @ {c:.2f}")
    return 1 if confident_wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
