"""Regression tests for AGV-45: music must stay paused after a stop/interrupt.

Hooks are async and unordered, so a trailing `resume` (PostToolUse etc.) can
land after `stop` — or a stray `play` can hit the state file after the daemon
was told to pause. Both must leave the music paused until the next real
user prompt (`play`).

Run with:  python -m unittest discover tests
"""
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import hook
import player

# Captured before any test's setUp() monkeypatches hook.ensure_bridge_alive,
# so tests can still exercise the real function's own error handling.
_REAL_ENSURE_BRIDGE_ALIVE = hook.ensure_bridge_alive


class FakeMusic:
    """Stands in for pygame.mixer.music; records playback state."""

    def __init__(self):
        self.state = "stopped"

    def load(self, path):
        pass

    def set_volume(self, volume):
        pass

    def play(self, loops=0):
        self.state = "playing"

    def pause(self):
        self.state = "paused"

    def unpause(self):
        self.state = "playing"

    def stop(self):
        self.state = "stopped"


def make_fake_pygame():
    music = FakeMusic()
    mixer = types.SimpleNamespace(init=lambda: None, quit=lambda: None, music=music)
    return types.SimpleNamespace(mixer=mixer), music


def wait_for(condition, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class TempFilesMixin(unittest.TestCase):
    """Points hook.py and player.py at per-test temp files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lethimcook-test-")
        for mod in (hook, player):
            mod.STATE_FILE = os.path.join(self.tmp, "state")
            mod.HEARTBEAT_FILE = os.path.join(self.tmp, "heartbeat")
            mod.STOP_FLAG_FILE = os.path.join(self.tmp, "stopped")
            mod.USER_PAUSE_FILE = os.path.join(self.tmp, "userpause")
            mod.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        player.LOCK_FILE = os.path.join(self.tmp, "lock")
        player.SONG = os.path.join(self.tmp, "song.mp3")
        with open(player.SONG, "w") as f:
            f.write("not a real mp3")
        # Hook tests must never launch a real daemon or touch the network.
        hook.spawn_player = lambda: None
        self.bridge_calls = []
        hook.ensure_bridge_alive = lambda: self.bridge_calls.append(1)
        hook.ensure_watcher_alive = lambda: None  # never spawn a real watcher

    def read_state(self):
        try:
            with open(hook.STATE_FILE) as f:
                return f.read().strip()
        except OSError:
            return None


class HookStateTests(TempFilesMixin):
    def test_resume_after_stop_stays_paused(self):
        hook.main("play")
        hook.main("stop")
        hook.main("resume")  # trailing PostToolUse after an interrupt
        self.assertEqual(self.read_state(), "pause")
        self.assertTrue(hook.stop_flag_set())

    def test_play_then_stop_ends_paused(self):
        hook.main("resume")  # writes play
        hook.main("stop")    # immediately followed by a stop
        self.assertEqual(self.read_state(), "pause")

    def test_user_prompt_lifts_hard_stop(self):
        hook.main("stop")
        hook.main("prompt")  # UserPromptSubmit
        self.assertEqual(self.read_state(), "play")
        self.assertFalse(hook.stop_flag_set())

    def test_prompt_and_play_ensure_bridge_alive(self):
        hook.main("prompt")  # UserPromptSubmit
        hook.main("play")    # explicit user play
        self.assertEqual(len(self.bridge_calls), 2)

    def test_mid_turn_resume_does_not_touch_bridge(self):
        hook.main("prompt")
        hook.main("resume")  # PostToolUse etc. - not a fresh prompt
        self.assertEqual(len(self.bridge_calls), 1)  # only the initial prompt

    def test_other_actions_never_touch_bridge(self):
        for action in ("pause", "wait", "stop", "quit", "off", "on"):
            hook.main(action)
        self.assertEqual(self.bridge_calls, [])

    def test_ensure_bridge_alive_swallows_import_errors(self):
        # Exercise the REAL function (not the mixin's tracking stub) to
        # prove its own try/except protects hook.py if bridge.py is
        # missing/broken - this must never take the hook down with it.
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "bridge":
                raise ImportError("no bridge module")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            _REAL_ENSURE_BRIDGE_ALIVE()  # must not raise

    def test_resume_after_notification_wait_plays(self):
        hook.main("prompt")
        hook.main("wait")    # Notification: waiting on permission (soft)
        hook.main("resume")  # PostToolUse: permission approved
        self.assertEqual(self.read_state(), "play")


class UserPauseLatchTests(TempFilesMixin):
    """A manual pause/quit must survive stray MID-TURN `resume` events (the
    'random play 2-3 seconds after I paused' bug), but a genuine new user
    `prompt` is explicit intent to resume and DOES start the music again."""

    def setUp(self):
        super().setUp()
        self.spawns = []
        hook.spawn_player = lambda: self.spawns.append(1)

    def test_manual_pause_survives_resume(self):
        hook.main("prompt")
        hook.main("pause")   # user typed it / pressed the menu button
        hook.main("resume")  # PostToolUse fires 2 seconds later...
        self.assertEqual(self.read_state(), "pause")  # ...and changes nothing

    def test_new_prompt_resumes_after_manual_pause(self):
        # Bug fix: a fresh query must auto-play, not stay stuck on pause.
        hook.main("pause")
        hook.main("prompt")  # user sends a new query
        self.assertEqual(self.read_state(), "play")
        self.assertFalse(hook.user_paused())

    def test_quit_survives_resume_but_next_prompt_resumes(self):
        hook.main("prompt")
        hook.main("quit")
        self.spawns.clear()
        hook.main("resume")  # stray mid-turn event must NOT respawn
        self.assertNotEqual(self.read_state(), "play")
        self.assertEqual(self.spawns, [])
        hook.main("prompt")  # but a real new query resumes
        self.assertEqual(self.read_state(), "play")

    def test_explicit_play_lifts_manual_pause(self):
        hook.main("pause")
        hook.main("play")  # explicit resume
        self.assertEqual(self.read_state(), "play")
        self.assertFalse(hook.user_paused())
        self.assertEqual(len(self.spawns), 1)

    def test_wait_does_not_set_the_latch(self):
        hook.main("wait")  # Notification pause is soft, not a manual pause
        self.assertFalse(hook.user_paused())


class DaemonTests(TempFilesMixin):
    def setUp(self):
        super().setUp()
        player.POLL_SECONDS = 0.02
        self.fake_pygame, self.music = make_fake_pygame()
        sys.modules["pygame"] = self.fake_pygame
        self.thread = threading.Thread(target=player.main, daemon=True)
        self.thread.start()

    def tearDown(self):
        with open(player.STATE_FILE, "w") as f:
            f.write("quit")
        self.thread.join(timeout=3)
        sys.modules.pop("pygame", None)

    def write_state(self, state):
        with open(player.STATE_FILE, "w") as f:
            f.write(state)

    def test_stray_play_after_stop_never_resumes(self):
        hook.main("play")
        self.assertTrue(wait_for(lambda: self.music.state == "playing"))

        hook.main("stop")
        self.assertTrue(wait_for(lambda: self.music.state == "paused"))

        # Race: a late async hook wins the state file with a raw "play"
        # while the stop flag is set. The daemon must not resume.
        self.write_state("play")
        time.sleep(player.POLL_SECONDS * 10)
        self.assertEqual(self.music.state, "paused")

        # Next real user prompt lifts the stop and music resumes.
        hook.main("prompt")
        self.assertTrue(wait_for(lambda: self.music.state == "playing"))

    def test_user_pause_latch_beats_stray_play_in_daemon(self):
        hook.main("play")
        self.assertTrue(wait_for(lambda: self.music.state == "playing"))

        hook.main("pause")  # manual pause: sets the latch
        self.assertTrue(wait_for(lambda: self.music.state == "paused"))

        # Race: something writes a raw "play" while the latch is set.
        # The daemon itself must refuse to resume.
        self.write_state("play")
        time.sleep(player.POLL_SECONDS * 10)
        self.assertEqual(self.music.state, "paused")

        # Only the user's explicit play resumes.
        hook.main("play")
        self.assertTrue(wait_for(lambda: self.music.state == "playing"))


if __name__ == "__main__":
    unittest.main()
