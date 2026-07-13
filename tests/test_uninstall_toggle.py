"""Tests for AGV-47: clean uninstall and the temporary disable toggle.

Covers:
* `hook.py off` / `on` — flipping "enabled" in config.json, silencing
  immediately, and suppressing play while disabled (no daemon spawns).
* The daemon honoring the "enabled" flag live, like volume.
* `setup.py --uninstall` internals — hook removal is surgical (other
  tools' hooks survive), idempotent, and temp files are cleaned up.

Run with:  python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
sys.path.insert(0, _REPO_ROOT)

import hook
import player
import setup


def wait_for(condition, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class TempPathsMixin(unittest.TestCase):
    """Points hook.py / player.py / setup.py at per-test temp files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lethimcook-agv47-")
        for mod in (hook, player):
            mod.STATE_FILE = os.path.join(self.tmp, "state")
            mod.HEARTBEAT_FILE = os.path.join(self.tmp, "heartbeat")
            mod.STOP_FLAG_FILE = os.path.join(self.tmp, "stopped")
            mod.USER_PAUSE_FILE = os.path.join(self.tmp, "userpause")
            mod.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        hook.ACTIVE_MARKER = os.path.join(self.tmp, "active")
        hook.activate()  # tests run with the master switch ON
        player.LOCK_FILE = os.path.join(self.tmp, "lock")
        player.SONG = os.path.join(self.tmp, "song.mp3")
        with open(player.SONG, "w") as f:
            f.write("not a real mp3")

        self.spawns = []
        hook.spawn_player = lambda: self.spawns.append(1)
        hook.ensure_bridge_alive = lambda: None  # no self-spawning in tests
        hook.ensure_watcher_alive = lambda: None

    def read_state(self):
        try:
            with open(hook.STATE_FILE) as f:
                return f.read().strip()
        except OSError:
            return None

    def read_config(self):
        with open(hook.CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)

    def write_config(self, config):
        with open(hook.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f)


class ToggleTests(TempPathsMixin):
    def test_off_pauses_and_disables(self):
        hook.main("play")
        self.assertEqual(self.read_state(), "play")
        hook.main("off")
        self.assertEqual(self.read_state(), "pause")
        self.assertFalse(self.read_config()["enabled"])

    def test_play_while_disabled_stays_quiet_and_spawns_nothing(self):
        hook.main("off")
        self.spawns.clear()
        hook.main("play")
        hook.main("resume")
        self.assertEqual(self.read_state(), "pause")
        self.assertEqual(self.spawns, [])

    def test_on_reenables_without_reinstall(self):
        hook.main("off")
        hook.main("on")
        self.assertTrue(self.read_config()["enabled"])
        hook.main("play")
        self.assertEqual(self.read_state(), "play")
        self.assertEqual(len(self.spawns), 1)

    def test_off_preserves_other_config_keys(self):
        self.write_config({"volume": 55})
        hook.main("off")
        config = self.read_config()
        self.assertEqual(config["volume"], 55)
        self.assertFalse(config["enabled"])


class DaemonToggleTests(TempPathsMixin):
    """The daemon must honor the enabled flag live, like volume."""

    def setUp(self):
        super().setUp()
        player.POLL_SECONDS = 0.02
        music = self.music = types.SimpleNamespace(state="stopped")
        music.load = lambda path: None
        music.set_volume = lambda volume: None
        music.play = lambda loops=0: setattr(music, "state", "playing")
        music.pause = lambda: setattr(music, "state", "paused")
        music.unpause = lambda: setattr(music, "state", "playing")
        music.stop = lambda: setattr(music, "state", "stopped")
        mixer = types.SimpleNamespace(init=lambda: None, quit=lambda: None, music=music)
        sys.modules["pygame"] = types.SimpleNamespace(mixer=mixer)

        self.config_time = time.time()
        self.thread = threading.Thread(target=player.main, daemon=True)
        self.thread.start()

    def tearDown(self):
        with open(player.STATE_FILE, "w") as f:
            f.write("quit")
        self.thread.join(timeout=3)
        sys.modules.pop("pygame", None)

    def write_config_bumped(self, config):
        """Write config with a guaranteed-newer mtime so the daemon reloads."""
        self.write_config(config)
        self.config_time += 2
        os.utime(player.CONFIG_FILE, (self.config_time, self.config_time))

    def test_disable_silences_live_and_reenable_resumes(self):
        hook.main("play")
        self.assertTrue(wait_for(lambda: self.music.state == "playing"))

        self.write_config_bumped({"volume": 100, "enabled": False})
        self.assertTrue(wait_for(lambda: self.music.state == "paused"))

        # state file still says "play" — re-enabling must resume instantly
        self.write_config_bumped({"volume": 100, "enabled": True})
        self.assertTrue(wait_for(lambda: self.music.state == "playing"))


class UninstallTests(TempPathsMixin):
    def setUp(self):
        super().setUp()
        self.settings_file = os.path.join(self.tmp, "settings.json")
        setup.claude_settings_path = lambda: self.settings_file
        setup.DAEMON_EXIT_TIMEOUT = 0.3

    def write_settings(self):
        ours = {
            "type": "command",
            "command": sys.executable,
            "args": [os.path.join("scripts", "hook.py"), "play"],
            "statusMessage": "lethimcook",
        }
        foreign = {"type": "command", "command": "black", "args": ["--check"]}
        settings = {
            "model": "opus",
            "hooks": {
                "UserPromptSubmit": [{"hooks": [ours]}],
                "PostToolUse": [{"hooks": [ours, foreign]}],
            },
        }
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(settings, f)

    def read_settings(self):
        with open(self.settings_file, encoding="utf-8") as f:
            return json.load(f)

    def test_remove_hooks_is_surgical(self):
        self.write_settings()
        setup.remove_hooks()
        settings = self.read_settings()
        self.assertNotIn("lethimcook", json.dumps(settings))
        self.assertEqual(settings["model"], "opus")  # untouched
        self.assertNotIn("UserPromptSubmit", settings["hooks"])  # emptied -> dropped
        foreign = settings["hooks"]["PostToolUse"][0]["hooks"]
        self.assertEqual(foreign, [{"type": "command", "command": "black", "args": ["--check"]}])
        self.assertTrue(os.path.exists(self.settings_file + ".bak"))

    def test_remove_hooks_drops_empty_hooks_key(self):
        self.write_settings()
        settings = self.read_settings()
        del settings["hooks"]["PostToolUse"]  # leave only our hooks
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(settings, f)
        setup.remove_hooks()
        self.assertNotIn("hooks", self.read_settings())  # zero residue

    def test_remove_hooks_idempotent_and_missing_file_ok(self):
        self.write_settings()
        setup.remove_hooks()
        setup.remove_hooks()  # second run: nothing of ours left, no error
        self.assertNotIn("lethimcook", json.dumps(self.read_settings()))
        os.remove(self.settings_file)
        setup.remove_hooks()  # missing file: no error

    def test_stop_daemon_and_clean_removes_temp_files(self):
        temp_files = (
            hook.STATE_FILE,
            hook.HEARTBEAT_FILE,
            hook.STOP_FLAG_FILE,
            hook.USER_PAUSE_FILE,
            player.LOCK_FILE,
        )
        for path in temp_files:
            with open(path, "w") as f:
                f.write("x")
        setup.stop_daemon_and_clean()
        for path in temp_files:
            self.assertFalse(os.path.exists(path), path)


if __name__ == "__main__":
    unittest.main()
