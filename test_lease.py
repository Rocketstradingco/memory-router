"""Check that a lease token, rather than an agent label, owns a target."""

import importlib.util
import os
import pathlib
import unittest


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


if __name__ == "__main__":
    unittest.main()
