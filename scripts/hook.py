"""Claude Code hook entry point: `python hook.py play|pause|quit`.

Writes the desired state to a temp file that player.py polls. On `play`,
spawns the player daemon (detached, hidden) if none is running. Must stay
fast — it runs on every hook event.
"""
import os
import subprocess
import sys
import tempfile
import time

TMP = tempfile.gettempdir()
STATE_FILE = os.path.join(TMP, "claude-thinking-song.state")
HEARTBEAT_FILE = os.path.join(TMP, "claude-thinking-song.heartbeat")


def heartbeat_fresh():
    try:
        return time.time() - os.path.getmtime(HEARTBEAT_FILE) < 5
    except OSError:
        return False


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "pause"

    with open(STATE_FILE, "w") as f:
        f.write(action)

    if action == "play" and not heartbeat_fresh():
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


if __name__ == "__main__":
    main()
