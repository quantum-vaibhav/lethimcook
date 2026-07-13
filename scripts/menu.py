"""Interactive terminal menu for lethimcook - no commands to remember.

A simple number-driven "menu card" that runs right in your terminal and
drives the exact same actions as the hooks/GUI (via controls.py):

    +--------------------------------------+
    |  [1] play          [4] mute / unmute |
    |  [2] pause         [5] volume        |
    |  [3] quit daemon   [6] refresh       |
    |  [q] leave the kitchen               |
    +--------------------------------------+

Pause and quit here are *sticky*: Claude Code hooks can never restart the
music behind your back - only your own [1] play does.

Launch:  python scripts/menu.py   (or menu.bat / bash menu.sh in the root)

Plain ASCII on purpose: Windows consoles often run cp1252 and choke on
emoji/box-drawing characters.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import controls  # noqa: E402


def describe(status):
    """One ASCII status line for the menu header."""
    if not status.get("active", True):
        head = "OFF (deactivated) - run setup.bat to turn it back on"
        return head
    if not status["installed"]:
        head = "not installed (run setup)"
    elif not status["enabled"]:
        head = "muted (config enabled=false)"
    elif status["user_paused"]:
        head = "paused by you (a new query or [1] play resumes)"
    elif status["playing"]:
        head = "playing"
    elif status["running"]:
        head = "paused"
    else:
        head = "idle (daemon not running)"
    line = "%s | volume %d%%" % (head, status["volume"])
    if not status["song"]:
        line += " | no song file yet"
    return line


MENU = """
  lethimcook - kitchen controls
  -----------------------------
  status: {status}

  [1] play          [4] mute / unmute
  [2] pause         [5] volume
  [3] refresh
  [q] quit lethimcook  (full stop - re-run setup.bat to use it again)
"""


def clean(raw):
    """Normalize console input: strip whitespace AND stray BOMs.

    Windows pipes can prefix the first line with a UTF-8 BOM. Depending on
    the stdin encoding python picks, that arrives either as U+FEFF or as
    the mojibake "\xef\xbb\xbf" - strip both, or the first menu choice a
    user pipes in reads as 'unknown'.
    """
    for bom in ("\ufeff", "\xef\xbb\xbf"):
        raw = raw.replace(bom, "")
    return raw.strip().lower()


def handle(choice, read=input):
    """Execute one menu choice; returns a feedback line (or None)."""
    if choice in ("3", ""):
        return None  # refresh: the loop reprints the status anyway
    # Everything below actually drives the music, which only works while
    # lethimcook is active. If it's been quit, point the user at setup.
    if not controls.is_active():
        return "lethimcook is OFF - run setup.bat to turn it back on"
    if choice == "1":
        controls.play()
        return "cooking - music on"
    if choice == "2":
        controls.pause()
        return "paused - a new query (or [1] play) starts it again"
    if choice == "4":
        enabled = controls.get_status()["enabled"]
        controls.set_enabled(not enabled)
        return "muted (install intact)" if enabled else "unmuted"
    if choice == "5":
        raw = clean(read("  volume 0-100: "))
        try:
            volume = int(raw)
        except ValueError:
            return "that's not a number"
        return "volume set to %d%%" % controls.set_volume(volume)
    return "unknown choice - pick 1-5 or q"


def main():
    while True:
        print(MENU.format(status=describe(controls.get_status())))
        try:
            choice = clean(input("  > "))
        except (EOFError, KeyboardInterrupt):
            # Closing the window / Ctrl+C just leaves lethimcook running.
            print()
            break
        if choice == "q":
            controls.deactivate()
            print("  -> lethimcook fully stopped. run setup.bat (or setup.sh)")
            print("     to use the music and menu again.")
            break
        feedback = handle(choice)
        if feedback:
            print("  -> " + feedback)
    print("  later, chef.")


if __name__ == "__main__":
    main()
