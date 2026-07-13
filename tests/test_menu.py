"""Tests for the interactive terminal menu.

menu.py is a thin dispatcher over controls.py, so these tests drive its
handle()/describe() functions against temp state/config files — the same
way the real menu drives the real system, minus the input() loop.

Run with:  python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
sys.path.insert(0, _REPO_ROOT)

import controls
import hook
import menu
import setup


class MenuTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lethimcook-menu-")
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
        hook._stop_background_helpers = lambda: None  # no real daemon/network
        setup.SONG = os.path.join(self.tmp, "song.mp3")
        setup.claude_settings_path = lambda: os.path.join(self.tmp, "settings.json")

    def read_state(self):
        try:
            with open(hook.STATE_FILE) as f:
                return f.read().strip()
        except OSError:
            return None


class HandleTests(MenuTestBase):
    def test_play_choice(self):
        menu.handle("2")  # pause first so play visibly changes things
        feedback = menu.handle("1")
        self.assertIn("music on", feedback)
        self.assertEqual(self.read_state(), "play")
        self.assertFalse(hook.user_paused())

    def test_pause_choice_sets_sticky_latch(self):
        menu.handle("1")
        feedback = menu.handle("2")
        self.assertIn("starts it again", feedback)
        self.assertEqual(self.read_state(), "pause")
        self.assertTrue(hook.user_paused())
        hook.main("resume")  # a stray mid-turn hook must change nothing
        self.assertEqual(self.read_state(), "pause")

    def test_deactivate_stops_and_gates_everything(self):
        # `q` in the menu calls deactivate(); afterwards the whole system is
        # off and menu actions no-op with a "run setup" nudge.
        menu.handle("1")
        controls.deactivate()
        self.assertFalse(hook.is_active())
        self.assertEqual(self.read_state(), "quit")  # player told to exit
        feedback = menu.handle("1")  # try to play while off
        self.assertIn("OFF", feedback)
        self.assertNotEqual(self.read_state(), "play")

    def test_mute_toggle_roundtrip(self):
        self.assertIn("muted", menu.handle("4"))
        self.assertFalse(hook.music_enabled())
        self.assertIn("unmuted", menu.handle("4"))
        self.assertTrue(hook.music_enabled())

    def test_volume_choice(self):
        feedback = menu.handle("5", read=lambda prompt: "55")
        self.assertIn("55", feedback)
        self.assertEqual(controls.get_volume(), 55)

    def test_volume_rejects_garbage(self):
        feedback = menu.handle("5", read=lambda prompt: "loud")
        self.assertIn("not a number", feedback)

    def test_unknown_choice(self):
        self.assertIn("unknown", menu.handle("9"))

    def test_refresh_is_silent(self):
        self.assertIsNone(menu.handle("3"))
        self.assertIsNone(menu.handle(""))

    def test_clean_strips_bom_whitespace_and_case(self):
        # Windows pipes can prefix the first line with a UTF-8 BOM; it
        # arrives as U+FEFF or as cp1252 mojibake depending on stdin encoding.
        self.assertEqual(menu.clean(chr(0xFEFF) + "2\n"), "2")
        self.assertEqual(menu.clean(b"\xef\xbb\xbf2".decode("cp1252")), "2")
        self.assertEqual(menu.clean("  Q "), "q")


class DescribeTests(MenuTestBase):
    def test_describe_user_paused(self):
        with open(setup.SONG, "w") as f:
            f.write("mp3")
        settings = {"hooks": {"UserPromptSubmit": [{"hooks": [{"statusMessage": "lethimcook"}]}]}}
        with open(setup.claude_settings_path(), "w", encoding="utf-8") as f:
            json.dump(settings, f)
        menu.handle("2")
        line = menu.describe(controls.get_status())
        self.assertIn("paused by you", line)

    def test_describe_is_ascii_only(self):
        # Windows consoles often run cp1252; the menu must never crash there.
        for status_line in (
            menu.describe(controls.get_status()),
            menu.MENU,
        ):
            status_line.encode("ascii")  # raises if any non-ascii sneaks in


if __name__ == "__main__":
    unittest.main()
