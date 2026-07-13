"""Tests for the localhost HTTP bridge (AGV-46).

The bridge must translate HTTP calls into the same state-file transitions
hook.py performs — including the AGV-45 hard-stop semantics — and must
reject web origins that are not allowlisted.

Also covers the autostart fix: bridge.is_running()/spawn_bridge() must be
idempotent (never double-launch), and /shutdown must terminate the bridge
for local callers only, never for a browser Origin.

Run with:  python -m unittest discover tests
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
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
        hook.USER_PAUSE_FILE = os.path.join(self.tmp, "userpause")
        hook.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        hook.spawn_player = lambda: None  # never launch a real daemon in tests
        hook.ensure_bridge_alive = lambda: None  # no self-spawning in tests
        hook.ensure_watcher_alive = lambda: None

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

    def test_shutdown_rejected_for_browser_origin(self):
        # A browser always sends an Origin header cross-origin; /shutdown
        # must be invisible to it (falls through to unknown-action 404).
        code, _, body = self.call("shutdown", origin="https://claude.ai")
        self.assertEqual(code, 404)
        self.assertEqual(body, {"error": "unknown action"})

    def test_shutdown_stops_server_for_local_caller(self):
        code, _, body = self.call("shutdown")  # no Origin -> local caller
        self.assertEqual(code, 200)
        self.assertEqual(body, {"ok": True})
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())  # serve_forever() actually exited


class SessionWatcherTests(unittest.TestCase):
    """The watcher must map session-file activity onto prompt/wait, with
    the same guarantees as real hooks (latch respected via hook.main, hard
    stops only overridden by clearly-newer activity from another surface).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lethimcook-watcher-")
        hook.STOP_FLAG_FILE = os.path.join(self.tmp, "stopped")
        self.session = os.path.join(self.tmp, "session.jsonl")
        self.sent = []
        self.watcher = bridge.SessionWatcher(
            send=self.sent.append,
            patterns=[os.path.join(self.tmp, "*.jsonl")],
            window=30,
            margin=10,
        )

    def touch_session(self, age_seconds=0):
        with open(self.session, "w") as f:
            f.write("x")
        stamp = time.time() - age_seconds
        os.utime(self.session, (stamp, stamp))

    def test_idle_with_no_files_sends_nothing(self):
        self.watcher.tick()
        self.watcher.tick()
        self.assertEqual(self.sent, [])

    def test_fresh_activity_sends_prompt_once(self):
        self.touch_session()
        self.watcher.tick()
        self.watcher.tick()  # still active: no repeat
        self.assertEqual(self.sent, ["prompt"])

    def test_going_quiet_sends_wait(self):
        self.touch_session()
        self.watcher.tick()
        self.touch_session(age_seconds=120)  # long past the window
        self.watcher.tick()
        self.assertEqual(self.sent, ["prompt", "wait"])

    def test_activity_well_past_a_hard_stop_lifts_it(self):
        self.touch_session()
        self.watcher.tick()  # prompt; watcher now active
        hook.set_stop_flag()  # e.g. Claude Code turn ended
        self.touch_session()  # cowork keeps cooking, mtime = now >> stop+10
        stale = time.time() - 60
        os.utime(hook.STOP_FLAG_FILE, (stale, stale))
        self.watcher.tick()
        self.assertEqual(self.sent, ["prompt", "prompt"])

    def test_trailing_append_within_margin_does_not_lift_stop(self):
        self.touch_session()
        self.watcher.tick()  # prompt; active
        hook.set_stop_flag()  # stop and the final append land together
        self.touch_session()
        self.watcher.tick()  # newest is NOT margin past the stop
        self.assertEqual(self.sent, ["prompt"])

    def test_watch_globs_cover_projects_and_cowork(self):
        patterns = " ".join(bridge.watch_globs())
        self.assertIn(os.path.join(".claude", "projects"), patterns)
        self.assertIn("local-agent-mode-sessions", patterns)


class IsRunningAndSpawnTests(unittest.TestCase):
    """bridge.is_running() / spawn_bridge() must be a safe, idempotent guard."""

    def test_is_running_false_when_port_free(self):
        # Bind-and-release to get a free ephemeral port with high confidence.
        probe = socket.socket()
        probe.bind((bridge.HOST, 0))
        port = probe.getsockname()[1]
        probe.close()
        self.assertFalse(bridge.is_running(port))

    def test_is_running_true_when_something_listening(self):
        listener = socket.socket()
        listener.bind((bridge.HOST, 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            self.assertTrue(bridge.is_running(port))
        finally:
            listener.close()

    @patch("bridge.subprocess.Popen")
    @patch("bridge.is_running", return_value=True)
    def test_spawn_bridge_skips_when_already_running(self, mock_is_running, mock_popen):
        self.assertFalse(bridge.spawn_bridge())
        mock_popen.assert_not_called()

    @patch("bridge.subprocess.Popen")
    @patch("bridge.is_running", return_value=False)
    def test_spawn_bridge_launches_when_not_running(self, mock_is_running, mock_popen):
        self.assertTrue(bridge.spawn_bridge())
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        self.assertTrue(args[-1].endswith("bridge.py"))


if __name__ == "__main__":
    unittest.main()
