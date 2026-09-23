"""HTTP checks for route input validation, without a Jev call."""

import importlib.util
import json
import os
import pathlib
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch


HERE = pathlib.Path(__file__).resolve().parent
os.environ["ROUTER_CONFIG"] = str(HERE / "config.json")
spec = importlib.util.spec_from_file_location("memory_router_app", HERE / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class RouteProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.H)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/route"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post(self, payload):
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=2) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_empty_and_misnamed_inputs_never_call_jev(self):
        with patch.object(app, "jev_decide") as jev:
            for payload in ({}, {"fact": "a real fact"}, {"text": "  "},
                            {"state": {}}, {"state": {"fact": "  "}}, []):
                with self.subTest(payload=payload):
                    status, result = self.post(payload)
                    self.assertEqual(status, 400)
                    self.assertIn("error", result)
            jev.assert_not_called()

    def test_valid_text_and_state_reach_jev(self):
        with patch.object(app, "jev_decide", return_value={"answers": {}}) as jev, \
                patch.object(app, "log_decision"):
            self.assertEqual(self.post({"text": "  shared network  "})[0], 200)
            self.assertEqual(self.post({"state": {"fact": "shared network"}})[0], 200)
            self.assertEqual(jev.call_args_list[0].args[0], {"text": "shared network"})
            self.assertEqual(jev.call_args_list[1].args[0], {"fact": "shared network"})


if __name__ == "__main__":
    unittest.main()
