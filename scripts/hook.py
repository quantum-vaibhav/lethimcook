"""Claude Code hook entry point: `python hook.py play|resume|pause|stop|quit`.

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

The hard-stop flag exists because hooks are async and unordered: a trailing
PostToolUse can land *after* Stop and would otherwise flip the music back on.
"""
import os
import subprocess
import sys
import tempfile
import time

TMP = tempfile.gettempdir()
STATE_FILE = os.path.join(TMP, "claude-thinking-song.state")
HEARTBEAT_FILE = os.path.join(TMP, "claude-thinking-song.heartbeat")
STOP_FLAG_FILE = os.path.join(TMP, "claude-thinking-song.stopped")


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

    write_state(action)

    if action == "play" and not heartbeat_fresh():
        spawn_player()


if __name__ == "__main__":
    main()
