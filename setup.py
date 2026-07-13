#!/usr/bin/env python3
"""One-click setup for lethimcook (Windows / macOS / Linux).

1. Installs pygame (audio playback) if missing.
2. Downloads the song via yt-dlp if thinking-song.mp3 is missing.
3. Merges the hooks into ~/.claude/settings.json with absolute paths
   for THIS machine (removing any hooks from a previous install).

`python setup.py --uninstall` reverses it: removes the hooks, shuts the
music daemon down, and deletes the temp state/lock/heartbeat files.
Idempotent - safe to run even if nothing is installed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

DAEMON_EXIT_TIMEOUT = 5  # seconds to wait for the daemon to honor "quit"

ROOT = os.path.dirname(os.path.abspath(__file__))
SONG = os.path.join(ROOT, "thinking-song.mp3")
HOOK_SCRIPT = os.path.join(ROOT, "scripts", "hook.py")
SONG_URL = "https://www.youtube.com/watch?v=9kT0oLBPiOw"  # Claude's Plan - Jeff Guo
MARKER = "lethimcook"

# Signatures identifying hooks from any previous install (incl. old names/versions)
LEGACY_SIGNATURES = [
    "start-music.ps1",
    "stop-music.ps1",
    "thinking-song",
    "thinkingSounds",
    "lethimcook",
    "claude-thinking-song",
    "hook.py",
]


def fail(msg):
    print("\n[X] " + msg)
    sys.exit(1)


def step(msg):
    print("\n==> " + msg)


def ensure_python_version():
    if sys.version_info < (3, 8):
        fail("Python 3.8+ is required (you have %s)." % sys.version.split()[0])


def ensure_pygame():
    step("Checking pygame (audio engine)...")
    try:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame  # noqa: F401
        print("    pygame already installed.")
        return
    except ImportError:
        pass
    print("    Installing pygame via pip...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user", "pygame", "--quiet"]
    )
    if result.returncode != 0:
        # --user is invalid inside virtualenvs; retry without it
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pygame", "--quiet"]
        )
    if result.returncode != 0:
        fail("Could not install pygame. Run manually: %s -m pip install pygame" % sys.executable)
    print("    pygame installed.")


def ensure_song():
    step("Checking song file...")
    if os.path.exists(SONG):
        print("    thinking-song.mp3 present.")
        return
    print("    thinking-song.mp3 missing - downloading with yt-dlp...")
    if not shutil.which("ffmpeg"):
        fail(
            "ffmpeg is required to download/convert the song.\n"
            "    Install it (winget install ffmpeg / brew install ffmpeg / apt install ffmpeg)\n"
            "    or copy a thinking-song.mp3 into: " + ROOT
        )
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user", "yt-dlp", "--quiet"]
    )
    result = subprocess.run(
        [
            sys.executable, "-m", "yt_dlp",
            "-x", "--audio-format", "mp3", "--audio-quality", "0",
            "-o", os.path.join(ROOT, "thinking-song.%(ext)s"),
            SONG_URL,
        ]
    )
    if result.returncode != 0 or not os.path.exists(SONG):
        fail("Song download failed. Copy any mp3 to: " + SONG)
    print("    Song downloaded.")


def is_ours(hook):
    blob = json.dumps(hook)
    return any(sig in blob for sig in LEGACY_SIGNATURES)


def strip_our_hooks(hooks_config):
    for event in list(hooks_config.keys()):
        entries = hooks_config[event]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
                entry["hooks"] = [h for h in entry["hooks"] if not is_ours(h)]
        hooks_config[event] = [
            e for e in entries
            if not (isinstance(e, dict) and e.get("hooks") == [])
        ]
        if not hooks_config[event]:
            del hooks_config[event]


def make_hook(action):
    return {
        "type": "command",
        "command": sys.executable,
        "args": [HOOK_SCRIPT, action],
        "async": True,
        "statusMessage": MARKER,
    }


def claude_settings_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def install_hooks():
    step("Registering hooks in ~/.claude/settings.json...")
    settings_path = claude_settings_path()
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)

    settings = {}
    if os.path.exists(settings_path):
        try:
            # utf-8-sig tolerates a BOM from Windows editors like Notepad
            with open(settings_path, encoding="utf-8-sig") as f:
                settings = json.load(f)
        except (ValueError, OSError):
            fail("Existing %s is not valid JSON - fix or delete it, then rerun." % settings_path)
        shutil.copy2(settings_path, settings_path + ".bak")
        print("    Backed up existing settings to settings.json.bak")

    hooks = settings.setdefault("hooks", {})
    strip_our_hooks(hooks)

    events = {
        "UserPromptSubmit": "prompt",   # prompt sent -> thinking (lifts hard stop, NOT manual pause)
        "PostToolUse": "resume",        # resume after tool ran - suppressed by hard stop / manual pause
        "PostToolUseFailure": "resume", # resume after a failed tool - Claude keeps thinking
        "PermissionDenied": "resume",   # resume after user denies - Claude keeps thinking
        "Notification": "wait",         # Claude is waiting for the user (soft pause, resumable)
        "Stop": "stop",                 # turn finished/interrupted - hard stop until next prompt
        "SessionEnd": "stop",           # session closed - hard stop until next prompt
    }
    for event, action in events.items():
        hooks.setdefault(event, []).append({"hooks": [make_hook(action)]})

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    print("    Hooks installed (python: %s)" % sys.executable)


def remove_hooks():
    step("Removing lethimcook hooks from ~/.claude/settings.json...")
    settings_path = claude_settings_path()
    if not os.path.exists(settings_path):
        print("    No settings file - nothing to remove.")
        return
    try:
        # utf-8-sig tolerates a BOM from Windows editors like Notepad
        with open(settings_path, encoding="utf-8-sig") as f:
            settings = json.load(f)
    except (ValueError, OSError):
        fail("Existing %s is not valid JSON - fix or delete it, then rerun." % settings_path)
    shutil.copy2(settings_path, settings_path + ".bak")
    print("    Backed up current settings to settings.json.bak")

    hooks = settings.get("hooks")
    if isinstance(hooks, dict):
        strip_our_hooks(hooks)
        if not hooks:
            del settings["hooks"]  # leave zero residue

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    print("    Hooks removed. Everything else in your settings is untouched.")


def ensure_bridge_running():
    step("Starting the local bridge (for web chat)...")
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import bridge

    if bridge.is_running():
        print("    Bridge already running on http://127.0.0.1:%d" % bridge.DEFAULT_PORT)
    else:
        bridge.spawn_bridge()
        print("    Bridge started on http://127.0.0.1:%d" % bridge.DEFAULT_PORT)
    print("    (Claude Code restarts it automatically if it's ever not running.)")


def activate_switch():
    step("Activating lethimcook...")
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import hook

    hook.activate()
    print("    Master switch ON. (Quit from the menu to fully stop it.)")


def ensure_watcher_running():
    step("Starting the Cowork watcher...")
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import watcher

    if watcher.cowork_sessions_root() is None:
        print("    No Cowork session store found - skipping (Claude Desktop not detected).")
        return
    if watcher.is_running():
        print("    Cowork watcher already running.")
    else:
        watcher.spawn_watcher()
        print("    Cowork watcher started (music follows Cowork turns).")
    print("    (Claude Code restarts it automatically if it's ever not running.)")


def stop_bridge():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import bridge

    if not bridge.is_running():
        return
    try:
        from urllib.request import Request, urlopen
        urlopen(Request("http://127.0.0.1:%d/shutdown" % bridge.DEFAULT_PORT, method="POST"), timeout=2)
        print("    Bridge stopped.")
    except OSError:
        pass  # nothing reachable - nothing to report


def stop_watcher():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import watcher

    if not watcher.is_running():
        return
    watcher.request_stop()
    deadline = time.time() + DAEMON_EXIT_TIMEOUT
    while time.time() < deadline and watcher.is_running():
        time.sleep(0.1)
    print("    Cowork watcher stopped.")


def stop_daemon_and_clean():
    step("Stopping the music daemon and cleaning temp files...")
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import hook
    import player

    if os.path.exists(hook.HEARTBEAT_FILE):
        hook.main("quit")  # daemon polls 5x/sec and exits on "quit"
        deadline = time.time() + DAEMON_EXIT_TIMEOUT
        while time.time() < deadline and os.path.exists(hook.HEARTBEAT_FILE):
            time.sleep(0.1)

    stop_bridge()
    stop_watcher()

    import watcher
    leftovers = 0
    for path in (
        hook.STATE_FILE,
        hook.HEARTBEAT_FILE,
        hook.STOP_FLAG_FILE,
        hook.USER_PAUSE_FILE,
        hook.ACTIVE_MARKER,  # master switch OFF
        player.LOCK_FILE,
        watcher.HEARTBEAT_FILE,
        watcher.STOP_FILE,
    ):
        try:
            os.remove(path)
            leftovers += 1
        except OSError:
            pass
    print("    Daemon stopped; %d temp file(s) removed." % leftovers)


def uninstall():
    print("lethimcook uninstall - kicking him out of the kitchen")
    print("=" * 50)
    remove_hooks()
    stop_daemon_and_clean()
    print("\n" + "=" * 50)
    print("Uninstalled. Restart Claude Code to drop the hooks, then")
    print("delete this folder whenever you like. No hard feelings.")


def main():
    parser = argparse.ArgumentParser(
        description="lethimcook setup - theme music while Claude cooks"
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="remove the hooks, stop the daemon, and clean up temp files",
    )
    args = parser.parse_args()

    ensure_python_version()

    if args.uninstall:
        uninstall()
        return

    print("lethimcook setup - theme music while Claude cooks")
    print("=" * 50)
    ensure_pygame()
    ensure_song()
    install_hooks()
    activate_switch()  # flip the master ON switch before starting daemons
    ensure_bridge_running()
    ensure_watcher_running()
    print("\n" + "=" * 50)
    print("Done! Restart Claude Code to activate the hooks.")
    print("Music starts when Claude is thinking, pauses when it")
    print("stops or waits for you, and resumes where it left off.")


if __name__ == "__main__":
    main()
