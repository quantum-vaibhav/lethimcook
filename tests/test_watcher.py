"""Tests for the Cowork audit-log watcher.

The watcher must turn Cowork's audit.jsonl event stream (user/assistant ->
generating, result -> done) into the same play/pause actions the hooks use,
edge-triggered and starting at the tail of a live session.

Run with:  python -m unittest discover tests
"""
import os
import sys
import tempfile
import threading
import time
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import hook
import watcher


class ClassifyTests(unittest.TestCase):
    def test_user_starts_assistant_continues_result_ends(self):
        self.assertEqual(watcher.classify('{"type":"user","message":{}}'), "start")
        self.assertEqual(watcher.classify('{"type":"assistant","message":{}}'), "cont")
        self.assertEqual(watcher.classify('{"type":"result","subtype":"success"}'), "end")

    def test_unknown_and_garbage_are_skipped(self):
        self.assertIsNone(watcher.classify('{"type":"rate_limit_event"}'))
        self.assertIsNone(watcher.classify("not json at all"))
        self.assertIsNone(watcher.classify(""))

    def test_system_events_do_not_start_music(self):
        # system init/status fire on session setup/teardown - they must NOT
        # start a turn, or closing the app starts playback out of nowhere.
        self.assertIsNone(watcher.classify('{"type":"system","subtype":"init"}'))
        self.assertIsNone(watcher.classify('{"type":"system","subtype":"status"}'))

    def test_new_turn_emits_prompt_and_result_emits_stop(self):
        in_turn, actions = watcher.actions_for(
            ['{"type":"user"}', '{"type":"assistant"}', '{"type":"result"}'], False)
        self.assertFalse(in_turn)
        self.assertEqual(actions, ["prompt", "stop"])  # one play-edge, one stop

    def test_joining_mid_turn_emits_resume_not_prompt(self):
        # First line we see is an assistant (we started mid-turn): "resume"
        # respects a manual pause, unlike "prompt".
        in_turn, actions = watcher.actions_for(['{"type":"assistant"}'], False)
        self.assertTrue(in_turn)
        self.assertEqual(actions, ["resume"])

    def test_no_respam_while_already_in_turn(self):
        in_turn, actions = watcher.actions_for(
            ['{"type":"assistant"}', '{"type":"assistant"}'], True)
        self.assertTrue(in_turn)
        self.assertEqual(actions, [])  # already in a turn -> nothing re-emitted

    def test_skip_only_batch_emits_nothing(self):
        self.assertEqual(watcher.actions_for(['{"type":"system"}', "junk"], False), (False, []))
        self.assertEqual(watcher.actions_for(["junk"], True), (True, []))


class NewestAuditTests(unittest.TestCase):
    def test_picks_most_recently_modified(self):
        root = tempfile.mkdtemp(prefix="lethimcook-watch-")
        a = os.path.join(root, "s1", "audit.jsonl")
        b = os.path.join(root, "s2", "audit.jsonl")
        for p in (a, b):
            os.makedirs(os.path.dirname(p))
            with open(p, "w") as f:
                f.write("{}\n")
        os.utime(a, (1000, 1000))
        os.utime(b, (2000, 2000))
        self.assertEqual(watcher.newest_audit(root), b)

    def test_none_when_no_audit(self):
        self.assertIsNone(watcher.newest_audit(tempfile.mkdtemp()))


class WatchLoopTests(unittest.TestCase):
    """Drive the real tail loop against a fake session dir."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="lethimcook-watchloop-")
        self.session = os.path.join(self.root, "sess")
        os.makedirs(self.session)
        self.audit = os.path.join(self.session, "audit.jsonl")
        with open(self.audit, "w", encoding="utf-8") as f:
            f.write('{"type":"result","subtype":"success"}\n')  # pre-existing history
        watcher.POLL_SECONDS = 0.02
        watcher.RESCAN_SECONDS = 0.0  # rescan every loop so the test is quick
        watcher.HEARTBEAT_FILE = os.path.join(self.root, "wh")
        watcher.STOP_FILE = os.path.join(self.root, "ws")
        self.emitted = []

    def append(self, *lines):
        with open(self.audit, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
            f.flush()

    def run_watch_in_thread(self, app_running=lambda: True):
        self._stop = False
        self.thread = threading.Thread(
            target=watcher.watch,
            kwargs={
                "root": self.root,
                "should_stop": lambda: self._stop,
                "emit": lambda action: self.emitted.append(action),
                "app_running": app_running,
            },
            daemon=True,
        )
        self.thread.start()

    def stop_watch(self):
        self._stop = True
        self.thread.join(timeout=3)

    def wait_until(self, predicate, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_does_not_replay_history_then_tracks_live_turn(self):
        self.run_watch_in_thread()
        try:
            # It started at the tail, so the pre-existing "result" is ignored.
            time.sleep(0.1)
            self.assertEqual(self.emitted, [])

            self.append('{"type":"user","message":{}}')     # you ask -> new turn
            self.assertTrue(self.wait_until(lambda: self.emitted == ["prompt"]))

            self.append('{"type":"assistant","message":{}}')  # still cooking
            time.sleep(0.1)
            self.assertEqual(self.emitted, ["prompt"])  # no spurious re-emit

            self.append('{"type":"result","subtype":"success"}')  # done
            self.assertTrue(self.wait_until(lambda: self.emitted == ["prompt", "stop"]))
        finally:
            self.stop_watch()

    def test_app_gone_pauses_immediately(self):
        # Claude Desktop closed/killed mid-turn: the watcher must silence,
        # not keep (or start) playing off a stale/interrupted session.
        watcher.APP_CHECK_SECONDS = 0.0  # check the app every loop
        self.alive = True
        self.run_watch_in_thread(app_running=lambda: self.alive)
        try:
            time.sleep(0.1)  # let the watcher seek to the file's tail first
            self.append('{"type":"user","message":{}}')
            self.assertTrue(self.wait_until(lambda: self.emitted[-1:] == ["prompt"]))
            self.alive = False  # user quits Claude from Task Manager
            self.assertTrue(self.wait_until(lambda: self.emitted[-1:] == ["stop"]))
        finally:
            self.stop_watch()

    def test_idle_timeout_pauses_interrupted_turn(self):
        # A turn that never gets a `result` (interrupted) must not play forever.
        watcher.IDLE_PAUSE_SECONDS = 0.4
        self.run_watch_in_thread()
        try:
            time.sleep(0.1)  # let the watcher seek to the file's tail first
            self.append('{"type":"assistant","message":{}}')  # generating...
            self.assertTrue(self.wait_until(lambda: "resume" in self.emitted))
            # ...then nothing more is ever written (Claude was interrupted)
            self.assertTrue(self.wait_until(lambda: self.emitted[-1:] == ["stop"], timeout=2))
        finally:
            self.stop_watch()


class EmitIntegrationTests(unittest.TestCase):
    """emit() must drive the real hook state machine (prompt/stop)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lethimcook-watch-emit-")
        hook.STATE_FILE = os.path.join(self.tmp, "state")
        hook.HEARTBEAT_FILE = os.path.join(self.tmp, "heartbeat")
        hook.STOP_FLAG_FILE = os.path.join(self.tmp, "stopped")
        hook.USER_PAUSE_FILE = os.path.join(self.tmp, "userpause")
        hook.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        hook.ACTIVE_MARKER = os.path.join(self.tmp, "active")
        hook.activate()  # tests run with the master switch ON
        hook.spawn_player = lambda: None
        hook.ensure_bridge_alive = lambda: None
        hook.ensure_watcher_alive = lambda: None

    def state(self):
        with open(hook.STATE_FILE) as f:
            return f.read().strip()

    def test_emit_prompt_plays_stop_pauses(self):
        watcher.emit("prompt")
        self.assertEqual(self.state(), "play")
        watcher.emit("stop")
        self.assertEqual(self.state(), "pause")

    def test_mid_turn_resume_respects_manual_pause(self):
        hook.main("pause")        # user paused via menu/CLI mid-cowork-turn
        watcher.emit("resume")    # an assistant line lands right after
        self.assertEqual(self.state(), "pause")  # manual pause still wins

    def test_new_turn_prompt_resumes_after_manual_pause(self):
        hook.main("pause")        # user paused
        watcher.emit("prompt")    # a genuinely new cowork turn begins
        self.assertEqual(self.state(), "play")   # new turn resumes, like Code


class LifecycleTests(unittest.TestCase):
    """Singleton heartbeat + stop-flag, mirroring the bridge's lifecycle."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lethimcook-watch-life-")
        watcher.HEARTBEAT_FILE = os.path.join(self.tmp, "wh")
        watcher.STOP_FILE = os.path.join(self.tmp, "ws")
        hook.ACTIVE_MARKER = os.path.join(self.tmp, "active")
        hook.activate()  # active, so the loop exits via the stop flag not the switch

    def test_is_running_reflects_heartbeat_freshness(self):
        self.assertFalse(watcher.is_running())
        with open(watcher.HEARTBEAT_FILE, "w") as f:
            f.write("x")
        self.assertTrue(watcher.is_running())
        os.utime(watcher.HEARTBEAT_FILE, (0, 0))  # ancient
        self.assertFalse(watcher.is_running())

    def test_request_stop_exits_the_watch_loop(self):
        watcher.RESCAN_SECONDS = 0.0
        watcher.POLL_SECONDS = 0.01
        empty_root = os.path.join(self.tmp, "sessions")
        os.makedirs(empty_root)
        thread = threading.Thread(
            target=watcher.watch, args=(empty_root,),
            kwargs={"emit": lambda g: None, "app_running": lambda: True},
            daemon=True,
        )
        thread.start()
        watcher.request_stop()  # default should_stop is _stop_requested
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())