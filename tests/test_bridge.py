"""Tests for the localhost HTTP bridge (AGV-46).

The bridge must translate HTTP calls into the same state-file transitions
hook.py performs — including the AGV-45 hard-stop semantics — and must
reject web origins that are not allowlisted.

Run with:  python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import bridge
import hook


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lethimcook-bridge-test-")
        hook.STATE_FILE = os.path.join(self.tmp, "state")
        hook.HEARTBEAT_FILE = os.path.join(self.tmp, "heartbeat")
        hook.STOP_FLAG_FILE = os.path.join(self.tmp, "stopped")
        hook.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        hook.spawn_player = lambda: None  # never launch a real daemon in tests

        self.server = ThreadingHTTPServer((bridge.HOST, 0), bridge.BridgeHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def call(self, path, origin=None, method="POST"):
        req = Request("http://%s:%d/%s" % (bridge.HOST, self.port, path), method=method)
        if origin:
            req.add_header("Origin", origin)
        try:
            with urlopen(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, dict(resp.headers), json.loads(raw or "{}")
        except HTTPError as err:
            raw = err.read().decode("utf-8")
            return err.code, dict(err.headers), json.loads(raw or "{}")

    def state(self):
        try:
            with open(hook.STATE_FILE) as f:
                return f.read().strip()
        except OSError:
            return None

    def test_play_writes_state(self):
        code, _, body = self.call("play")
        self.assertEqual(code, 200)
        self.assertEqual(body, {"ok": True, "action": "play"})
        self.assertEqual(self.state(), "play")

    def test_stop_then_resume_stays_paused(self):
        self.call("play")
        self.call("stop")
        self.call("resume")  # must be suppressed by the hard stop
        self.assertEqual(self.state(), "pause")
        code, _, body = self.call("status", method="GET")
        self.assertEqual(code, 200)
        self.assertEqual(body, {"state": "pause", "stopped": True, "enabled": True})

    def test_play_lifts_hard_stop(self):
        self.call("stop")
        self.call("play")
        _, _, body = self.call("status", method="GET")
        self.assertEqual(body, {"state": "play", "stopped": False, "enabled": True})

    def test_unknown_action_is_404_and_writes_nothing(self):
        code, _, body = self.call("selfdestruct")
        self.assertEqual(code, 404)
        self.assertEqual(body, {"error": "unknown action"})
        self.assertIsNone(self.state())

    def test_disallowed_web_origin_rejected(self):
        code, headers, body = self.call("play", origin="https://evil.example")
        self.assertEqual(code, 403)
        self.assertEqual(body, {"error": "origin not allowed"})
        self.assertIsNone(self.state())
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_allowed_origin_gets_cors_header(self):
        code, headers, _ = self.call("pause", origin="https://claude.ai")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "https://claude.ai")

    def test_preflight_includes_private_network_header(self):
        code, headers, _ = self.call("play", origin="https://claude.ai", method="OPTIONS")
        self.assertEqual(code, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Private-Network"), "true")
        self.assertIsNone(self.state())  # preflight must not trigger the action

    def test_index_lists_endpoints(self):
        code, _, body = self.call("", method="GET")
        self.assertEqual(code, 200)
        self.assertEqual(body["service"], "lethimcook-bridge")
        self.assertIn("status", body["endpoints"])


if __name__ == "__main__":
    unittest.main()
