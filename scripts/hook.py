"""Claude Code hook entry point: `python hook.py play|resume|pause|stop|quit|on|off`.

Writes the desired state to a temp file that player.py polls. On `play`,
spawns the player daemon (detached, hidden) if none is running. Must stay
fast — it runs on every hook event.

Actions:
    play    user prompted (UserPromptSubmit) — clears any hard stop, starts music
    resume  mid-turn event (PostToolUse etc.) — plays only if not hard-stopped
    pause   Claude is waiting on the user (Notification) — resumable
    stop    turn ended / interrupted (Stop, SessionEnd) — hard stop: stray
            `resume` events are suppressed until the next `play`
    quit    kill the daemon
    off     temporary disable: sets "enabled": false in config.json and
            silences the music; the install stays intact
    on      re-enable after `off`; music returns on the next play/resume

The hard-stop flag exists because hooks are async and unordered: a trailing
PostToolUse can land *after* Stop and would otherwise flip the music back on.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

TMP = tempfile.gettempdir()
STATE_FILE = os.path.join(TMP, "claude-thinking-song.state")
HEARTBEAT_FILE = os.path.join(TMP, "claude-thinking-song.heartbeat")
STOP_FLAG_FILE = os.path.join(TMP, "claude-thinking-song.stopped")

PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.json")
DEFAULT_CONFIG = {"volume": 100, "enabled": True}


def load_config():
    try:
        # utf-8-sig tolerates a BOM from Windows editors like Notepad
        with open(CONFIG_FILE, encoding="utf-8-sig") as f:
            config = json.load(f)
        return config if isinstance(config, dict) else {}
    except (OSError, ValueError):
        return {}


def music_enabled():
    return bool(load_config().get("enabled", True))


def set_enabled(value):
    config = load_config() or dict(DEFAULT_CONFIG)
    config["enabled"] = value
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def heartbeat_fresh():
    try:
        return time.time() - os.path.getmtime(HEARTBEAT_FILE) < 5
    except OSError:
        return False


def stop_flag_set():
    return os.path.exists(STOP_FLAG_FILE)


def set_stop_flag():
    with open(STOP_FLAG_FILE, "w") as f:
        f.write(str(time.time()))


def clear_stop_flag():
    try:
        os.remove(STOP_FLAG_FILE)
    except OSError:
        pass


def write_state(state):
    with open(STATE_FILE, "w") as f:
        f.write(state)


def spawn_player():
    player = os.path.join(os.path.dirname(os.path.abspath(__file__)), "player.py")
    kwargs = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NO_WINDOW
        kwargs["creationflags"] = 0x00000008 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, player],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def main(action=None):
    if action is None:
        action = sys.argv[1] if len(sys.argv) > 1 else "pause"

    if action == "off":
        set_enabled(False)
        write_state("pause")  # silence now; the daemon stays installed
        return
    if action == "on":
        set_enabled(True)
        return  # music returns on the next play/resume event

    if action == "play":
        # Explicit user intent: lifts a hard stop.
        clear_stop_flag()
    elif action == "stop":
        set_stop_flag()
        action = "pause"
    elif action == "resume":
        if stop_flag_set():
            return  # user stopped this turn; ignore stray mid-turn events
        action = "play"

    if action == "play" and not music_enabled():
        action = "pause"  # temporarily disabled: stay quiet, spawn nothing

    write_state(action)

    if action == "play" and not heartbeat_fresh():
        spawn_player()


if __name__ == "__main__":
    main()
