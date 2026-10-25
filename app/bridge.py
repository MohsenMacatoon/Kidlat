"""
Local bridge between the Firefox extension and the Kidlat app.

Listens only on 127.0.0.1 (your own PC). Web pages cannot use it: every
request must carry the X-Kidlat header and come from a browser extension
(or from Kidlat itself), otherwise it is rejected.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.0"


def _allowed_origin(origin):
    return (not origin or origin == "null"
            or origin.startswith("moz-extension://")
            or origin.startswith("chrome-extension://"))


class Bridge:
    def __init__(self, port, on_add, on_show):
        self.port = port
        self.on_add = on_add          # called with a dict (or list of dicts)
        self.on_show = on_show        # bring window to front
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, obj):
                body = b"" if code == 204 else json.dumps(obj).encode()
                self.send_response(code)
                origin = self.headers.get("Origin")
                if origin and _allowed_origin(origin):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers",
                                     "Content-Type, X-Kidlat")
                    self.send_header("Access-Control-Allow-Methods",
                                     "GET, POST, OPTIONS")
                if code != 204:
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _ok(self):
                if not _allowed_origin(self.headers.get("Origin")):
                    self._send(403, {"error": "forbidden"})
                    return False
                if self.headers.get("X-Kidlat") != "1":
                    self._send(403, {"error": "missing header"})
                    return False
                return True

            def do_OPTIONS(self):
                if _allowed_origin(self.headers.get("Origin")):
                    self._send(204, {})
                else:
                    self._send(403, {"error": "forbidden"})

            def do_GET(self):
                if not self._ok():
                    return
                if self.path == "/ping":
                    self._send(200, {"app": "kidlat", "version": VERSION})
                elif self.path == "/show":
                    bridge.on_show()
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._ok():
                    return
                if self.path != "/add":
                    self._send(404, {"error": "not found"})
                    return
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    if n > 5_000_000:
                        raise ValueError("too large")
                    data = json.loads(self.rfile.read(n) or b"{}")
                    items = data if isinstance(data, list) else [data]
                    items = [i for i in items if isinstance(i, dict)
                             and str(i.get("url", "")).startswith(("http://", "https://"))]
                    if not items:
                        raise ValueError("no valid http(s) url")
                except (ValueError, json.JSONDecodeError) as e:
                    self._send(400, {"error": str(e)})
                    return
                bridge.on_add(items)
                self._send(200, {"ok": True, "count": len(items)})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()


def ping_existing(port, path="/ping"):
    """Return True if a Kidlat instance is already running on this port."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     headers={"X-Kidlat": "1"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as r:
            return json.loads(r.read()).get("app") == "kidlat" or path != "/ping"
    except Exception:  # noqa: BLE001
        return False
