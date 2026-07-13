"""Local HTTP bridge + session watcher: drive the thinking-music daemon
from surfaces without Claude Code hooks (Cowork, claude.ai web chat,
scripts).

Claude Code drives the player via lifecycle hooks (hook.py). Surfaces that
have no hook mechanism are covered here, two ways:

1. The HTTP server: translates localhost requests into the exact same
   state-file actions hook.py performs (used by the claude.ai userscript
   and anything else that can curl).
2. The session watcher (a background thread): Cowork and desktop Claude
   sessions write their transcript files to disk while the assistant is
   working. The watcher polls those files' mtimes - fresh writes mean
   "the assistant is cooking" (play), silence means it's done (pause).
   That gives Cowork automatic music with no hooks and no browser.

setup.py starts this automatically, and hook.py re-spawns it on every
UserPromptSubmit if it isn't already up (e.g. after a reboot) - so once
you've run setup once, it stays available without a separate terminal.

    python scripts/bridge.py            # http://127.0.0.1:48765
    python scripts/bridge.py --port N
    python scripts/bridge.py --no-watch # HTTP only, no session watcher

Endpoints (GET or POST) - same verbs and semantics as hook.py:
    /play    explicit user play - overrides a manual pause and a hard stop
    /prompt  a turn started (surface-driven) - lifts a hard stop only
    /resume  soft resume - ignored after a hard stop or manual pause
    /pause   manual pause - sticks until an explicit /play
    /wait    soft pause - resumable
    /stop    hard stop - stays silent until the next /prompt or /play
    /quit    shut the player daemon down (sticks like /pause)
    /off     temporary disable ("enabled": false in config.json)
    /on      re-enable after /off
    /status  {"state": "...", "stopped": bool, "enabled": bool}

Security posture:
    * Binds 127.0.0.1 only - never reachable from the network.
    * Web pages get CORS approval only if their Origin is allowlisted
      (claude.ai); requests from any other web origin are rejected (403).
    * Requests carry no data beyond the action name in the path; nothing
      is logged or stored.
"""
import argparse
import glob
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import hook

HOST = "127.0.0.1"
DEFAULT_PORT = 48765
ACTIONS = ("play", "prompt", "resume", "pause", "wait", "stop", "quit", "on", "off")
ALLOWED_WEB_ORIGINS = ("https://claude.ai",)
CONNECT_TIMEOUT = 0.2  # seconds - keep the liveness check snappy

# --- session watcher tuning ----------------------------------------------
ACTIVITY_WINDOW = 30       # a transcript touched within this = "cooking"
WATCH_POLL_SECONDS = 2     # how often the watcher looks
STOP_OVERRIDE_MARGIN = 10  # activity must outlive a hard stop by this much


def is_running(port=DEFAULT_PORT, timeout=CONNECT_TIMEOUT):
    """Best-effort check: is *something* accepting connections on the port.

    Good enough to decide "should I spawn a bridge" without adding real
    latency - a full HTTP round trip isn't needed, just proof the port
    is taken (which our own bind-conflict handling in main() also guards).
    """
    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def spawn_bridge(port=DEFAULT_PORT):
    """Start the bridge as a detached background process if not already up.

    Returns True if a new process was spawned, False if one was already
    running. Safe to call unconditionally and often - a live bridge means
    this is a fast no-op (one local socket probe).
    """
    if is_running(port):
        return False
    script = os.path.abspath(__file__)
    args = [sys.executable, script]
    if port != DEFAULT_PORT:
        args += ["--port", str(port)]
    kwargs = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NO_WINDOW
        kwargs["creationflags"] = 0x00000008 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    return True


def read_state():
    try:
        with open(hook.STATE_FILE) as f:
            return f.read().strip()
    except OSError:
        return "pause"


def watch_globs():
    """Shallow glob patterns for local session transcripts, per platform.

    * ~/.claude/projects/*/*.jsonl - Claude Code session transcripts
      (verified: appended per event while the assistant works, untouched
      when idle).
    * .../Claude/local-agent-mode-sessions/*/*/local_*.json - Cowork
      session stores (verified: written during activity, no idle
      heartbeat).

    Deliberately shallow and specific: Cowork session dirs contain whole
    project copies we must not recurse into every two seconds.
    """
    home = os.path.expanduser("~")
    patterns = [os.path.join(home, ".claude", "projects", "*", "*.jsonl")]
    desktop_bases = [
        os.environ.get("APPDATA"),                                # Windows
        os.path.join(home, "Library", "Application Support"),     # macOS
        os.path.join(home, ".config"),                            # Linux
    ]
    for base in desktop_bases:
        if base:
            patterns.append(
                os.path.join(base, "Claude", "local-agent-mode-sessions",
                             "*", "*", "local_*.json")
            )
    return patterns


def newest_session_mtime(patterns=None):
    """The most recent mtime across all watched session files (0.0 if none)."""
    newest = 0.0
    for pattern in patterns or watch_globs():
        for path in glob.iglob(pattern):
            try:
                newest = max(newest, os.path.getmtime(path))
            except OSError:
                pass  # deleted between glob and stat
    return newest


class SessionWatcher:
    """Turns local session-file activity into play/pause, hook-free.

    Sends `prompt` when any watched session starts writing (respects the
    user's manual-pause latch and lifts hard stops, same as a real
    UserPromptSubmit), and `wait` (soft pause) when everything goes quiet.

    One subtlety: a Claude Code `Stop` fires a hard stop, but a Cowork
    session may still be cooking. If activity keeps arriving well past the
    hard stop (STOP_OVERRIDE_MARGIN), the watcher lifts it - the margin
    keeps the trailing transcript append of the stopping turn itself from
    un-pausing the music.
    """

    def __init__(self, send=None, patterns=None,
                 window=ACTIVITY_WINDOW, margin=STOP_OVERRIDE_MARGIN):
        self.send = send or hook.main
        self.patterns = patterns
        self.window = window
        self.margin = margin
        self.active = False

    def _stop_flag_mtime(self):
        try:
            return os.path.getmtime(hook.STOP_FLAG_FILE)
        except OSError:
            return 0.0

    def tick(self, now=None):
        now = time.time() if now is None else now
        newest = newest_session_mtime(self.patterns)
        active = newest > 0 and (now - newest) < self.window

        if active and not self.active:
            self.send("prompt")
        elif not active and self.active:
            self.send("wait")
        elif (active and hook.stop_flag_set()
                and newest > self._stop_flag_mtime() + self.margin):
            self.send("prompt")  # another surface kept cooking past the stop

        self.active = active

    def run_forever(self):
        while True:
            try:
                self.tick()
            except Exception:
                pass  # the watcher must never take the bridge down
            time.sleep(WATCH_POLL_SECONDS)


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # requests carry nothing worth logging

    def _cors_origin(self):
        """The request's Origin if allowlisted, else None.

        Non-browser clients (curl, scripts) send no Origin at all.
        """
        origin = self.headers.get("Origin")
        return origin if origin in ALLOWED_WEB_ORIGINS else None

    def _origin_allowed(self):
        origin = self.headers.get("Origin")
        return origin is None or origin in ALLOWED_WEB_ORIGINS

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # CORS preflight. Chrome additionally requires the private-network
        # header before letting an https page call into 127.0.0.1.
        self.send_response(204)
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin not allowed"})
            return
        action = urlparse(self.path).path.strip("/")
        if action == "":
            self._send_json(
                200,
                {"service": "lethimcook-bridge", "endpoints": list(ACTIONS) + ["status"]},
            )
        elif action == "status":
            self._send_json(
                200,
                {
                    "state": read_state(),
                    "stopped": hook.stop_flag_set(),
                    "enabled": hook.music_enabled(),
                },
            )
        elif action == "shutdown" and self.headers.get("Origin") is None:
            # Terminates this bridge *process* (distinct from /quit, which
            # only stops the music daemon). Local-only: browsers always send
            # an Origin header on cross-origin fetches, so this falls through
            # to the 404 below for any web caller. Used by setup.py --uninstall.
            self._send_json(200, {"ok": True})
            self.server.shutdown()
        elif action in ACTIONS:
            hook.main(action)
            self._send_json(200, {"ok": True, "action": action})
        else:
            self._send_json(404, {"error": "unknown action"})


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="lethimcook HTTP bridge for surfaces without Claude Code hooks"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help="port to listen on at 127.0.0.1 (default: %(default)s)",
    )
    parser.add_argument(
        "--no-watch", action="store_true",
        help="serve HTTP only; skip the Cowork/session-file watcher",
    )
    args = parser.parse_args(argv)

    try:
        server = ThreadingHTTPServer((HOST, args.port), BridgeHandler)
    except OSError:
        # Another bridge (or something else) already owns this port - most
        # likely we lost a race with another spawn_bridge() caller. Treat it
        # as already-running rather than crashing.
        print("lethimcook bridge already running on http://%s:%d" % (HOST, args.port))
        return

    if not args.no_watch:
        # The port bind above makes this bridge the singleton, so exactly
        # one watcher runs machine-wide.
        threading.Thread(target=SessionWatcher().run_forever, daemon=True).start()

    print("lethimcook bridge on http://%s:%d  (Ctrl+C to stop)" % (HOST, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbridge stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
