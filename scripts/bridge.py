"""Local HTTP bridge: drive the thinking-music daemon from surfaces
without Claude Code hooks (claude.ai web chat, Cowork fallback, scripts).

Claude Code drives the player via lifecycle hooks (hook.py). Surfaces that
have no hook mechanism call this tiny localhost-only HTTP server instead;
it translates each request into the exact same state-file action hook.py
performs, so the daemon (player.py) stays the single shared audio engine.

    python scripts/bridge.py            # http://127.0.0.1:48765
    python scripts/bridge.py --port N

Endpoints (GET or POST):
    /play    a turn started - lifts a hard stop, starts the daemon
    /resume  soft resume - ignored after a hard stop
    /pause   soft pause - resumable
    /stop    hard stop - stays silent until the next /play
    /quit    shut the player daemon down
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
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import hook

HOST = "127.0.0.1"
DEFAULT_PORT = 48765
ACTIONS = ("play", "resume", "pause", "stop", "quit", "on", "off")
ALLOWED_WEB_ORIGINS = ("https://claude.ai",)


def read_state():
    try:
        with open(hook.STATE_FILE) as f:
            return f.read().strip()
    except OSError:
        return "pause"


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
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((HOST, args.port), BridgeHandler)
    print("lethimcook bridge on http://%s:%d  (Ctrl+C to stop)" % (HOST, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbridge stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
