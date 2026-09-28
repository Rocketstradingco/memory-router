"""Check that a lease token, rather than an agent label, owns a target."""

import importlib.util
import json
import os
import pathlib
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch


HERE = pathlib.Path(__file__).resolve().parent
os.environ["ROUTER_CONFIG"] = str(HERE / "config.json")
spec = importlib.util.spec_from_file_location("memory_router_app", HERE / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class LeaseOwnership(unittest.TestCase):
    def test_agent_label_cannot_replace_an_active_lease(self):
        app._locks.clear()
        first = app.acquire("CLAUDE.md", "same-agent", 60)
        self.assertTrue(first["granted"])
        second = app.acquire("CLAUDE.md", "same-agent", 60)
        self.assertFalse(second["granted"])
        self.assertEqual(second["holder"], "same-agent")
        self.assertTrue(app.renew(first["lease"], 60)["renewed"])
        self.assertTrue(app.release(first["lease"])["released"])
        self.assertTrue(app.acquire("CLAUDE.md", "same-agent", 60)["granted"])
        app._locks.clear()


class DefaultTarget(unittest.TestCase):
    def test_omitted_target_uses_the_configured_default(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.H)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/lock/acquire"

        def acquire(payload):
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)

        try:
            app._locks.clear()
            with patch.object(app, "LOCK_TARGET", "shared.md"):
                self.assertEqual(acquire({"agent": "a"})["target"], "shared.md")
                # A caller naming the same file explicitly must collide with it.
                self.assertFalse(acquire({"agent": "b", "target": "shared.md"})["granted"])
        finally:
            app._locks.clear()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
