"""Logic layer behind the friendly controls (tray / GUI).

This module reinvents nothing: it drives the exact same actions the hooks
use (`hook.main`), reads the same state/config files, and shells out to
`setup.py` for install/uninstall. The GUI (`gui.py`) is a thin front-end
that only calls the functions here, so all behaviour stays unit-testable
without opening a window.

Every path is read live from the `hook` / `setup` modules, so the daemon
and the controls always agree on where state lives.
"""
import json
import os
import shutil
import subprocess
import sys

# hook.py lives in scripts/ (this dir); setup.py lives in the repo root above
# it. Put both on the path so imports work no matter the launcher's cwd.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
for _p in (_SCRIPTS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hook  # noqa: E402  (path set up above)
import setup  # noqa: E402


def _read_state():
    try:
        with open(hook.STATE_FILE) as f:
            return f.read().strip()
    except OSError:
        return "pause"


def get_volume():
    """Current volume as an int 0-100 (defaults to 100)."""
    try:
        return max(0, min(100, int(hook.load_config().get("volume", 100))))
    except (TypeError, ValueError):
        return 100


def song_present():
    return os.path.isfile(setup.SONG)


def is_installed():
    """True if any lethimcook hook is registered in ~/.claude/settings.json."""
    try:
        with open(setup.claude_settings_path(), encoding="utf-8-sig") as f:
            settings = json.load(f)
    except (OSError, ValueError):
        return False
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for single in entry.get("hooks", []):
                if setup.is_ours(single):
                    return True
    return False


def get_status():
    """Everything the UI needs to render, in one snapshot.

    `playing` is the derived truth the user cares about: the daemon is
    alive, the state file says play, and the soundtrack isn't disabled.
    """
    running = hook.heartbeat_fresh()
    state = _read_state()
    enabled = hook.music_enabled()
    return {
        "running": running,
        "state": state,
        "enabled": enabled,
        "playing": running and state == "play" and enabled,
        "volume": get_volume(),
        "song": song_present(),
        "installed": is_installed(),
    }


# --- playback (in-process, instant) --------------------------------------

def play():
    hook.main("play")


def pause():
    hook.main("pause")


def toggle_play():
    """Flip play/pause based on the current state; returns the new state."""
    if _read_state() == "play":
        pause()
    else:
        play()
    return _read_state()


def quit_daemon():
    hook.main("quit")


# --- temporary enable/disable --------------------------------------------

def enable():
    hook.main("on")


def disable():
    hook.main("off")


def set_enabled(value):
    enable() if value else disable()


# --- volume & song -------------------------------------------------------

def set_volume(percent):
    """Clamp to 0-100, persist to config.json (preserving other keys)."""
    percent = max(0, min(100, int(percent)))
    config = hook.load_config() or dict(hook.DEFAULT_CONFIG)
    config["volume"] = percent
    with open(hook.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    return percent


def set_song(path):
    """Copy a user-chosen .mp3 in as the thinking song. Returns its path."""
    if not path or not os.path.isfile(path):
        raise ValueError("song file not found: %s" % path)
    if os.path.splitext(path)[1].lower() != ".mp3":
        raise ValueError("song must be an .mp3 file")
    # Selecting the file that's already installed is a harmless no-op.
    if os.path.abspath(path) != os.path.abspath(setup.SONG):
        shutil.copy2(path, setup.SONG)
    return setup.SONG


# --- install / uninstall (isolated subprocess) ---------------------------
# setup.py may download the song or call sys.exit on error; running it as a
# child process keeps those side effects from taking the GUI down with them.

def _setup_command(*args):
    return [sys.executable, os.path.join(setup.ROOT, "setup.py"), *args]


def install():
    """Run the installer. Returns the completed process (check .returncode)."""
    return subprocess.run(_setup_command(), capture_output=True, text=True)


def uninstall():
    """Run the uninstaller. Returns the completed process."""
    return subprocess.run(_setup_command("--uninstall"), capture_output=True, text=True)
