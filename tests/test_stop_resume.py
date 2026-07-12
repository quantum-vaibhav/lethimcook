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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import hook
import player


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
        player.LOCK_FILE = os.path.join(self.tmp, "lock")
        player.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        player.SONG = os.path.join(self.tmp, "song.mp3")
        with open(player.SONG, "w") as f:
            f.write("not a real mp3")
        # Hook tests must never launch a real daemon.
        hook.spawn_player = lambda: None

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
        hook.main("play")  # UserPromptSubmit
        self.assertEqual(self.read_state(), "play")
        self.assertFalse(hook.stop_flag_set())

    def test_resume_without_stop_plays(self):
        hook.main("play")
        hook.main("pause")   # Notification: waiting on permission
        hook.main("resume")  # PostToolUse: permission approved
        self.assertEqual(self.read_state(), "play")


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
        hook.main("play")
        self.assertTrue(wait_for(lambda: self.music.state == "playing"))


if __name__ == "__main__":
    unittest.main()
