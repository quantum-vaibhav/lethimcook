"""Background music daemon for Claude Code "thinking music".

Loops thinking-song.mp3 forever, but pauses/resumes based on a state file
that hook.py writes. Pause preserves playback position, so the song always
plays through to the end and only restarts when it naturally finishes.

Singleton: an exclusive lock file plus a heartbeat file (touched every poll)
ensure only one daemon runs even if several Claude sessions fire hooks at once.

Volume comes from config.json in the project root ("volume": 0-100) and is
re-read while playing, so edits take effect within a second — no restart needed.
"""
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

DEFAULT_VOLUME = 100         # percent, used if config.json is missing/invalid
POLL_SECONDS = 0.2
HEARTBEAT_FRESH_SECONDS = 5
EXIT_AFTER_PAUSED_SECONDS = 2 * 60 * 60  # daemon exits after 2h of silence

TMP = tempfile.gettempdir()
STATE_FILE = os.path.join(TMP, "claude-thinking-song.state")
HEARTBEAT_FILE = os.path.join(TMP, "claude-thinking-song.heartbeat")
LOCK_FILE = os.path.join(TMP, "claude-thinking-song.lock")

PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
SONG = os.path.join(PROJECT_ROOT, "thinking-song.mp3")
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.json")


def read_volume():
    """Volume from config.json as 0.0-1.0. Accepts "volume": 0-100."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            percent = float(json.load(f).get("volume", DEFAULT_VOLUME))
    except (OSError, ValueError, TypeError):
        percent = DEFAULT_VOLUME
    return max(0.0, min(100.0, percent)) / 100.0


def config_mtime():
    try:
        return os.path.getmtime(CONFIG_FILE)
    except OSError:
        return 0


def touch(path):
    with open(path, "a"):
        os.utime(path, None)


def heartbeat_fresh():
    try:
        return time.time() - os.path.getmtime(HEARTBEAT_FILE) < HEARTBEAT_FRESH_SECONDS
    except OSError:
        return False


def read_state():
    try:
        with open(STATE_FILE) as f:
            return f.read().strip()
    except OSError:
        return "pause"


def acquire_lock():
    for _ in range(2):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            if heartbeat_fresh():
                return False  # a live daemon owns the lock
            try:
                os.remove(LOCK_FILE)  # stale lock from a dead daemon
            except OSError:
                return False
    return False


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def main():
    if not os.path.exists(SONG):
        return
    if not acquire_lock():
        return
    try:
        touch(HEARTBEAT_FILE)
        import pygame

        pygame.mixer.init()
        pygame.mixer.music.load(SONG)
        pygame.mixer.music.set_volume(read_volume())
        last_config_mtime = config_mtime()

        started = False
        playing = False
        paused_since = time.time()

        while True:
            touch(HEARTBEAT_FILE)

            mtime = config_mtime()
            if mtime != last_config_mtime:
                last_config_mtime = mtime
                pygame.mixer.music.set_volume(read_volume())

            state = read_state()

            if state == "quit":
                break

            if state == "play":
                if not started:
                    pygame.mixer.music.play(loops=-1)  # play full, loop on finish
                    started = True
                    playing = True
                elif not playing:
                    pygame.mixer.music.unpause()  # resume where it left off
                    playing = True
                paused_since = None
            else:  # pause / unknown
                if playing:
                    pygame.mixer.music.pause()
                    playing = False
                if paused_since is None:
                    paused_since = time.time()
                elif time.time() - paused_since > EXIT_AFTER_PAUSED_SECONDS:
                    break

            time.sleep(POLL_SECONDS)

        pygame.mixer.music.stop()
        pygame.mixer.quit()
    finally:
        release_lock()
        try:
            os.remove(HEARTBEAT_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    main()
