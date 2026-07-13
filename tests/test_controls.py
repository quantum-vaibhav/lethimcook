"""Tests for the tray/GUI logic layer (AGV-48).

The GUI is a thin front-end; all behaviour lives in controls.py, so these
tests exercise the real actions against temp state/config/settings files
without opening a window or spawning a daemon.

Run with:  python -m unittest discover tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
sys.path.insert(0, _REPO_ROOT)

import controls
import hook
import setup


class ControlsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lethimcook-agv48-")
        hook.STATE_FILE = os.path.join(self.tmp, "state")
        hook.HEARTBEAT_FILE = os.path.join(self.tmp, "heartbeat")
        hook.STOP_FLAG_FILE = os.path.join(self.tmp, "stopped")
        hook.USER_PAUSE_FILE = os.path.join(self.tmp, "userpause")
        hook.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        hook.spawn_player = lambda: None  # never launch a real daemon
        hook.ensure_bridge_alive = lambda: None  # no self-spawning in tests
        hook.ensure_watcher_alive = lambda: None

        self.song = os.path.join(self.tmp, "thinking-song.mp3")
        setup.SONG = self.song
        self.settings = os.path.join(self.tmp, "settings.json")
        setup.claude_settings_path = lambda: self.settings

    def write_config(self, config):
        with open(hook.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f)

    def read_config(self):
        with open(hook.CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)

    def read_state(self):
        try:
            with open(hook.STATE_FILE) as f:
                return f.read().strip()
        except OSError:
            return None


class PlaybackTests(ControlsTestBase):
    def test_play_and_pause(self):
        controls.play()
        self.assertEqual(self.read_state(), "play")
        controls.pause()
        self.assertEqual(self.read_state(), "pause")

    def test_toggle_play_flips_state(self):
        controls.pause()
        self.assertEqual(controls.toggle_play(), "play")
        self.assertEqual(controls.toggle_play(), "pause")

    def test_disabled_play_stays_quiet(self):
        controls.disable()
        controls.play()  # hook maps play->pause while disabled
        self.assertEqual(self.read_state(), "pause")


class ToggleTests(ControlsTestBase):
    def test_enable_disable_roundtrip(self):
        controls.disable()
        self.assertFalse(hook.music_enabled())
        controls.enable()
        self.assertTrue(hook.music_enabled())

    def test_set_enabled_bool(self):
        controls.set_enabled(False)
        self.assertFalse(hook.music_enabled())
        controls.set_enabled(True)
        self.assertTrue(hook.music_enabled())


class VolumeTests(ControlsTestBase):
    def test_set_volume_clamps_and_persists(self):
        self.assertEqual(controls.set_volume(150), 100)
        self.assertEqual(self.read_config()["volume"], 100)
        self.assertEqual(controls.set_volume(-5), 0)
        self.assertEqual(controls.set_volume(42), 42)
        self.assertEqual(controls.get_volume(), 42)

    def test_set_volume_preserves_enabled(self):
        self.write_config({"volume": 100, "enabled": False})
        controls.set_volume(30)
        config = self.read_config()
        self.assertEqual(config["volume"], 30)
        self.assertFalse(config["enabled"])

    def test_get_volume_defaults_when_missing(self):
        self.assertEqual(controls.get_volume(), 100)


class SongTests(ControlsTestBase):
    def _make_mp3(self, name):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(b"ID3 fake mp3 bytes")
        return path

    def test_set_song_copies_file(self):
        src = self._make_mp3("mytrack.mp3")
        result = controls.set_song(src)
        self.assertEqual(result, self.song)
        self.assertTrue(os.path.isfile(self.song))
        self.assertTrue(controls.song_present())

    def test_set_song_rejects_missing(self):
        with self.assertRaises(ValueError):
            controls.set_song(os.path.join(self.tmp, "nope.mp3"))

    def test_set_song_rejects_non_mp3(self):
        src = os.path.join(self.tmp, "notes.txt")
        with open(src, "w") as f:
            f.write("hi")
        with self.assertRaises(ValueError):
            controls.set_song(src)

    def test_set_song_same_file_is_noop(self):
        # Selecting the already-installed song must not raise SameFileError.
        with open(self.song, "wb") as f:
            f.write(b"ID3 existing")
        self.assertEqual(controls.set_song(self.song), self.song)
        self.assertTrue(controls.song_present())


class InstalledTests(ControlsTestBase):
    def _write_settings(self, obj):
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_is_installed_true_for_our_hook(self):
        self._write_settings({
            "hooks": {
                "UserPromptSubmit": [{"hooks": [{"statusMessage": "lethimcook"}]}],
            }
        })
        self.assertTrue(controls.is_installed())

    def test_is_installed_false_for_foreign_only(self):
        self._write_settings({
            "hooks": {"PostToolUse": [{"hooks": [{"command": "ruff"}]}]}
        })
        self.assertFalse(controls.is_installed())

    def test_is_installed_false_when_no_file(self):
        self.assertFalse(controls.is_installed())


class StatusTests(ControlsTestBase):
    def test_status_reports_playing_only_when_all_true(self):
        # daemon alive
        with open(hook.HEARTBEAT_FILE, "w") as f:
            f.write("x")
        controls.play()
        status = controls.get_status()
        self.assertTrue(status["running"])
        self.assertTrue(status["playing"])
        self.assertTrue(status["enabled"])

    def test_status_not_playing_when_disabled(self):
        with open(hook.HEARTBEAT_FILE, "w") as f:
            f.write("x")
        controls.play()
        controls.disable()
        status = controls.get_status()
        self.assertFalse(status["playing"])
        self.assertFalse(status["enabled"])

    def test_status_not_playing_when_daemon_dead(self):
        controls.play()  # state=play but no heartbeat
        status = controls.get_status()
        self.assertFalse(status["running"])
        self.assertFalse(status["playing"])


class ImportIsolationTests(unittest.TestCase):
    """Regression guard: the GUI launcher only puts scripts/ on sys.path, so
    controls.py must add the repo root itself to `import setup`. This test
    reproduces that launch context in a subprocess (neutral cwd, only scripts/
    on the path) — the in-process tests can't, because they add the repo root.
    """

    def test_controls_imports_with_only_scripts_on_path(self):
        scripts = os.path.join(_REPO_ROOT, "scripts")
        code = (
            "import sys, os; sys.path.insert(0, r'%s'); import controls; "
            "print(os.path.basename(controls.setup.__file__))" % scripts
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=tempfile.gettempdir(),  # neutral: repo root NOT implicitly on path
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "setup.py")


class SetupCommandTests(ControlsTestBase):
    def test_install_command_targets_setup_py(self):
        cmd = controls._setup_command()
        self.assertEqual(cmd[0], sys.executable)
        self.assertTrue(cmd[1].endswith("setup.py"))
        self.assertEqual(len(cmd), 2)

    def test_uninstall_command_passes_flag(self):
        cmd = controls._setup_command("--uninstall")
        self.assertEqual(cmd[-1], "--uninstall")
        self.assertTrue(cmd[1].endswith("setup.py"))


if __name__ == "__main__":
    unittest.main()
