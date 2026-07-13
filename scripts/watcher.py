"""Session watcher for Claude Desktop's Cowork mode.

Cowork does NOT run Claude Code's lifecycle hooks (it's a sandboxed agent
session), so hook.py never fires for it. But every Cowork turn is recorded
live in an `audit.jsonl` stream using the Claude Agent SDK message schema:

    {"type":"user", ...}        you sent a message      -> music plays
    {"type":"assistant", ...}   Claude is responding     -> music plays
    {"type":"result","subtype":"success", ...}  turn done -> music pauses

This daemon tails the newest active audit.jsonl and translates those events
into the exact same actions the hooks use (via hook.main), so Cowork drives
the one shared player just like Code mode does.

    python scripts/watcher.py

It is edge-triggered (only acts when the generating/idle state flips) and
respects the manual-pause latch, so `menu.py`/CLI pauses still stick.
"""
import glob
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hook  # noqa: E402

POLL_SECONDS = 0.5
RESCAN_SECONDS = 3.0          # how often to look for a newer active session
HEARTBEAT_FRESH_SECONDS = 5
IDLE_PAUSE_SECONDS = 30       # no audit writes this long -> assume turn ended
APP_CHECK_SECONDS = 8.0       # how often to confirm Claude Desktop is running
EXIT_AFTER_APP_GONE_SECONDS = 30  # quit the watcher once the app stays gone

# SDK message types. Only user/assistant mean "a turn is actively happening";
# `system` (init/status) fires on session setup/teardown and must NOT start
# music, or the app closing can trigger playback out of nowhere.
DONE_TYPES = {"result"}
ACTIVE_TYPES = {"user", "assistant"}

_TMP = tempfile.gettempdir()
HEARTBEAT_FILE = os.path.join(_TMP, "claude-thinking-song.watcher-heartbeat")
STOP_FILE = os.path.join(_TMP, "claude-thinking-song.watcher-stop")


def _no_window_kwargs():
    """subprocess kwargs that keep Windows from flashing a console window.

    Without CREATE_NO_WINDOW, running a console tool (tasklist) from our
    hidden background daemon pops a visible cmd window every single call -
    which, at one call every few seconds, makes the machine unusable.
    """
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # hide the window
    return {"creationflags": 0x08000000, "startupinfo": startupinfo}  # CREATE_NO_WINDOW


def claude_desktop_running():
    """Best-effort check that the Claude Desktop app is actually running.

    The watcher is a detached process that outlives the app, so without this
    an orphaned watcher could start music after you've closed/killed Claude.
    Any error -> assume running (never falsely silence a live session).
    """
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/NH"],
                capture_output=True, text=True, timeout=4, **_no_window_kwargs(),
            ).stdout.lower()
            return "claude.exe" in out
        out = subprocess.run(["pgrep", "-if", "claude"], capture_output=True, text=True, timeout=4)
        return out.returncode == 0
    except Exception:
        return True


def cowork_sessions_root():
    """Per-OS path to Claude Desktop's Cowork session store, or None."""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        candidate = os.path.join(base, "Claude") if base else None
    elif sys.platform == "darwin":
        candidate = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Claude")
    else:
        candidate = os.path.join(os.path.expanduser("~"), ".config", "Claude")
    if not candidate:
        return None
    root = os.path.join(candidate, "local-agent-mode-sessions")
    return root if os.path.isdir(root) else None


def newest_audit(root):
    """The most recently modified audit.jsonl under the session store."""
    if not root:
        return None
    newest, newest_mtime = None, -1.0
    for path in glob.iglob(os.path.join(root, "**", "audit.jsonl"), recursive=True):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = path, mtime
    return newest


def classify(line):
    """Map one audit line to a turn phase, or None to skip it.

        "start"  a `user` line   -> a new turn begins
        "cont"   an `assistant` line -> the turn is continuing
        "end"    a `result` line -> the turn is complete
        None     anything else (system/rate_limit/garbage)

    Tolerant on purpose: a malformed trailing write is skipped, not fatal.
    """
    import json
    try:
        event_type = json.loads(line).get("type")
    except (ValueError, AttributeError):
        return None
    if event_type in DONE_TYPES:
        return "end"
    if event_type == "user":
        return "start"
    if event_type == "assistant":
        return "cont"
    return None


def actions_for(new_lines, in_turn):
    """Fold new audit lines into (new_in_turn, [actions to emit]).

    A new turn (`user`) emits "prompt" — explicit intent that resumes even
    a manual pause, just like a Code-mode prompt. Joining a turn already in
    progress (`assistant` first) emits "resume", which respects a manual
    pause. Once we're in a turn, further lines don't re-emit (no spam), so a
    manual pause mid-turn is NOT undone by the next assistant line. `result`
    ends the turn with "stop".
    """
    actions = []
    for line in new_lines:
        kind = classify(line)
        if kind is None:
            continue
        if kind == "end":
            if in_turn:
                actions.append("stop")
                in_turn = False
        elif not in_turn:  # start or cont beginning a turn we weren't tracking
            actions.append("prompt" if kind == "start" else "resume")
            in_turn = True
    return in_turn, actions


def emit(action):
    # Drive the shared player through the same state machine the hooks use.
    hook.main(action)


def is_running():
    """True if a live watcher is touching the heartbeat file."""
    try:
        return time.time() - os.path.getmtime(HEARTBEAT_FILE) < HEARTBEAT_FRESH_SECONDS
    except OSError:
        return False


def spawn_watcher():
    """Start the watcher detached if one isn't already running.

    Returns True if a new process was spawned, False if already up. Safe to
    call unconditionally and often - a live watcher makes this a fast no-op.
    """
    if is_running():
        return False
    try:
        os.remove(STOP_FILE)  # clear any leftover stop request
    except OSError:
        pass
    script = os.path.abspath(__file__)
    kwargs = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NO_WINDOW
        kwargs["creationflags"] = 0x00000008 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    return True


def request_stop():
    """Ask a running watcher to exit (used by uninstall)."""
    with open(STOP_FILE, "w") as f:
        f.write(str(time.time()))


def _stop_requested():
    return os.path.exists(STOP_FILE)


def watch(root, should_stop=_stop_requested, emit=emit, start_at_end=True,
          app_running=claude_desktop_running):
    """Tail the newest audit.jsonl under `root`, emitting play/pause edges.

    Separated from main() so tests can drive it with a fake root, a
    `should_stop` predicate, and a stubbed `app_running` check instead of
    an infinite loop touching the real process table.
    """
    audit_path = None
    offset = 0
    in_turn = False
    last_rescan = 0.0
    last_activity = time.time()
    last_app_check = 0.0
    app_up = True
    app_gone_since = None

    while not should_stop():
        try:
            with open(HEARTBEAT_FILE, "a"):
                os.utime(HEARTBEAT_FILE, None)
        except OSError:
            pass

        now = time.time()

        # Is Claude Desktop still running? An orphaned watcher must never
        # start music after the app is closed/killed.
        if now - last_app_check >= APP_CHECK_SECONDS:
            last_app_check = now
            app_up = app_running()
            if app_up:
                app_gone_since = None
            elif app_gone_since is None:
                app_gone_since = now
        if not app_up:
            if in_turn:
                in_turn = False
                emit("stop")  # app gone -> silence immediately
            if app_gone_since is not None and now - app_gone_since > EXIT_AFTER_APP_GONE_SECONDS:
                return  # stop the orphaned watcher entirely
            time.sleep(POLL_SECONDS)
            continue

        if now - last_rescan >= RESCAN_SECONDS:
            last_rescan = now
            latest = newest_audit(root)
            if latest and latest != audit_path:
                # New active session: start at its end so we don't replay
                # a finished conversation and spuriously start the music.
                audit_path = latest
                try:
                    offset = os.path.getsize(audit_path) if start_at_end else 0
                except OSError:
                    offset = 0

        if audit_path:
            try:
                size = os.path.getsize(audit_path)
                if size < offset:
                    offset = 0  # file rotated/truncated
                if size > offset:
                    with open(audit_path, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(offset)
                        chunk = f.read()
                        offset = f.tell()
                    new_lines = [ln for ln in chunk.splitlines() if ln.strip()]
                    last_activity = now
                    in_turn, actions = actions_for(new_lines, in_turn)
                    for action in actions:
                        emit(action)
            except OSError:
                pass

        # Fallback: a turn that never got a `result` (e.g. Claude was
        # interrupted) must not play forever. If we've been mid-turn with no
        # audit writes for a while, assume the turn ended.
        if in_turn and now - last_activity > IDLE_PAUSE_SECONDS:
            in_turn = False
            emit("stop")

        time.sleep(POLL_SECONDS)


def main():
    root = cowork_sessions_root()
    if root is None:
        print("No Cowork session store found - is Claude Desktop installed?")
        return
    print("lethimcook cowork watcher on %s  (Ctrl+C to stop)" % root)
    try:
        watch(root)
    except KeyboardInterrupt:
        print("\nwatcher stopped.")
    finally:
        for path in (HEARTBEAT_FILE, STOP_FILE):
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
