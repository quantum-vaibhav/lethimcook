"""Claude Code hook entry point and user CLI:
`python hook.py play|pause|quit|on|off` (you) / `prompt|resume|wait|stop` (hooks).

Writes the desired state to a temp file that player.py polls. On play,
spawns the player daemon (detached, hidden) if none is running. Must stay
fast — it runs on every hook event.

Hook-fired actions (registered in ~/.claude/settings.json by setup.py):
    prompt  user prompted (UserPromptSubmit) — a fresh turn: clears the hard
            stop AND the manual pause, and starts the music
    resume  mid-turn event (PostToolUse etc.) — plays only if not
            hard-stopped and not manually paused
    wait    Claude is waiting on the user (Notification) — soft, resumable
    stop    turn ended / interrupted (Stop, SessionEnd) — hard stop: stray
            `resume` events are suppressed until the next prompt

User commands (CLI / terminal menu / GUI / bridge):
    play    explicit user intent — overrides everything: lifts the manual
            pause AND any hard stop, starts the music
    pause   manual pause — sticks THROUGH mid-turn `resume` events; your next
            prompt (or `play`) starts it again
    quit    kill the daemon — sticks the same way `pause` does
    off     temporary disable ("enabled": false in config.json) — the durable
            "stay off until I say on"; survives new prompts
    on      re-enable after `off`

Two guard files exist because hooks are async and unordered:
  * stop flag  — a trailing PostToolUse landing after Stop must not flip
    the music back on (cleared by the next prompt).
  * user latch — a manual pause/quit must survive stray mid-turn `resume`
    events (cleared by an explicit `play` or the next real `prompt`).
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
USER_PAUSE_FILE = os.path.join(TMP, "claude-thinking-song.userpause")

PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.json")
DEFAULT_CONFIG = {"volume": 100, "enabled": True}

# Master ON/OFF switch. This persistent marker file is the difference between
# "installed" and "actually running": setup.py creates it, and quitting from
# the menu (or `hook.py deactivate`) removes it. While it's absent EVERY hook
# and command is a no-op — no music, no daemons — until the next setup. This
# is what makes "quit" actually stay quit across sessions and reboots.
ACTIVE_MARKER = os.path.join(PROJECT_ROOT, ".lethimcook-active")


def is_active():
    return os.path.exists(ACTIVE_MARKER)


def activate():
    with open(ACTIVE_MARKER, "w") as f:
        f.write(str(time.time()))


def _stop_background_helpers():
    """Best-effort shutdown of the cowork watcher and the bridge.

    The missing marker also makes each self-exit, but ask them directly for
    speed. Isolated into its own function so tests can stub the real
    process/network side effects.
    """
    try:
        import watcher
        watcher.request_stop()
    except Exception:
        pass
    try:
        import bridge
        from urllib.request import Request, urlopen
        urlopen(Request("http://127.0.0.1:%d/shutdown" % bridge.DEFAULT_PORT, method="POST"), timeout=2)
    except Exception:
        pass


def deactivate():
    """Full stop: remove the switch and shut every background piece down."""
    try:
        os.remove(ACTIVE_MARKER)
    except OSError:
        pass
    clear_user_pause()
    clear_stop_flag()
    write_state("quit")  # the player daemon exits on this
    _stop_background_helpers()


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


def user_paused():
    return os.path.exists(USER_PAUSE_FILE)


def set_user_pause():
    with open(USER_PAUSE_FILE, "w") as f:
        f.write(str(time.time()))


def clear_user_pause():
    try:
        os.remove(USER_PAUSE_FILE)
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


def ensure_bridge_alive():
    """Best-effort: keep the web-chat HTTP bridge running.

    Runs once per real UserPromptSubmit so the bridge survives reboots
    without the user having to remember to restart it by hand. Must never
    break the hook itself, so any failure here is swallowed.
    """
    try:
        import bridge
        bridge.spawn_bridge()
    except Exception:
        pass


def ensure_watcher_alive():
    """Best-effort: keep the Cowork audit-log watcher running.

    Cowork doesn't fire hooks, so it can't self-heal - but a Code-mode
    prompt (which does fire this hook) revives the watcher for whenever
    the user switches to Cowork. Swallows all errors like the bridge does.
    """
    try:
        import watcher
        if watcher.cowork_sessions_root() is not None:
            watcher.spawn_watcher()
    except Exception:
        pass


def main(action=None):
    if action is None:
        action = sys.argv[1] if len(sys.argv) > 1 else "pause"

    # Master switch. `activate` (run by setup) turns the whole system on;
    # `deactivate` turns it fully off. While off, everything else is a
    # no-op, so hooks can fire all day and nothing plays or spawns.
    if action == "activate":
        activate()
        return
    if action == "deactivate":
        deactivate()
        return
    if not is_active():
        return  # deactivated -> dormant until the next setup

    if action == "off":
        set_enabled(False)
        write_state("pause")  # silence now; the daemon stays installed
        return
    if action == "on":
        set_enabled(True)
        return  # music returns on the next play/resume event

    requested = action

    if action == "play":
        # Explicit user command: overrides a manual pause AND a hard stop.
        clear_user_pause()
        clear_stop_flag()
    elif action == "prompt":
        # UserPromptSubmit: a fresh user turn is explicit intent to resume,
        # so it lifts BOTH a turn-level stop and a manual pause. (Only stray
        # mid-turn `resume` events still respect a manual pause — see below.)
        clear_stop_flag()
        clear_user_pause()
        action = "play"
    elif action == "stop":
        set_stop_flag()
        action = "pause"
    elif action == "resume":
        if stop_flag_set() or user_paused():
            return  # stray mid-turn event after a stop or a manual pause
        action = "play"
    elif action == "wait":
        action = "pause"  # Notification: soft pause, resumable mid-turn
    elif action == "pause":
        set_user_pause()  # manual pause sticks until an explicit play
    elif action == "quit":
        set_user_pause()  # a quit daemon must not be respawned by hooks

    if action == "play" and not music_enabled():
        action = "pause"  # temporarily disabled: stay quiet, spawn nothing

    write_state(action)

    if action == "play" and not heartbeat_fresh():
        spawn_player()

    if requested in ("play", "prompt"):
        ensure_bridge_alive()
        ensure_watcher_alive()


if __name__ == "__main__":
    main()
