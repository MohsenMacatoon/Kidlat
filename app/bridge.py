"""
Local bridge between the Firefox extension and the Kidlat app.

Listens only on 127.0.0.1 (your own PC). Web pages cannot use it: every
request must carry the X-Kidlat header and come from a browser extension
(or from Kidlat itself), otherwise it is rejected.
"""

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.0"


def _allowed_origin(origin):
    return (not origin or origin == "null"
            or origin.startswith("moz-extension://")
            or origin.startswith("chrome-extension://"))


def _allowed_host(host):
    """Only requests addressed to this PC. Stops web pages that point their own
    domain at 127.0.0.1 ("DNS rebinding") from talking to Kidlat."""
    if not host:
        return True
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return name.lower() in ("127.0.0.1", "localhost", "[::1]")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows "reuse address" would let a second program (or a second Kidlat)
    # listen on the same port. Ask for the port for ourselves instead.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        super().server_bind()


class Bridge:
    def __init__(self, port, on_add, on_show, recorder=None):
        self.port = port
        self.recorder = recorder      # saves ⏺ Record recordings
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
                                     "Content-Type, X-Kidlat, X-Rec-Id, X-Rec-Seq, X-Rec-Pos")
                    self.send_header("Access-Control-Allow-Methods",
                                     "GET, POST, OPTIONS")
                if code != 204:
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _ok(self):
                if not _allowed_host(self.headers.get("Host")):
                    self._send(403, {"error": "wrong host"})
                    return False
                if not _allowed_origin(self.headers.get("Origin")):
                    self._send(403, {"error": "forbidden"})
                    return False
                if self.headers.get("X-Kidlat") != "1":
                    self._send(403, {"error": "missing header"})
                    return False
                return True

            def do_OPTIONS(self):
                if _allowed_origin(self.headers.get("Origin")) and \
                        _allowed_host(self.headers.get("Host")):
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
                if self.path.startswith("/rec/"):
                    self._recording()
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

            def _recording(self):
                rec = bridge.recorder
                if rec is None:
                    self._send(404, {"error": "recording not available"})
                    return
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    if n < 0 or n > 64_000_000:
                        raise ValueError("piece too large")
                    body = self.rfile.read(n) if n else b""
                    if self.path == "/rec/append":
                        pos = self.headers.get("X-Rec-Pos")
                        ok = rec.append(self.headers.get("X-Rec-Id", ""),
                                        int(self.headers.get("X-Rec-Seq", "0")), body,
                                        float(pos) if pos else None)
                        self._send(200 if ok else 404, {"ok": ok})
                        return
                    data = json.loads(body or b"{}")
                    if self.path == "/rec/start":
                        try:
                            speed = min(4.0, max(1.0, float(data.get("speed") or 1)))
                        except (TypeError, ValueError):
                            speed = 1.0
                        self._send(200, rec.start(str(data.get("title") or "")[:150],
                                                  str(data.get("url") or ""),
                                                  str(data.get("session") or "") or None,
                                                  speed))
                    elif self.path == "/rec/reopen":
                        res = rec.reopen(str(data.get("session") or ""), str(data.get("file") or ""),
                                         int(data.get("size") or 0))
                        self._send(200 if res else 404, res or {"id": None})
                    elif self.path == "/rec/finish":
                        pos = rec.finish(str(data.get("id") or ""))
                        self._send(200, {"ok": pos is not None, "pos": pos})
                    elif self.path == "/rec/done":
                        sid = str(data.get("session") or "")
                        threading.Thread(target=rec.done, args=(sid,), daemon=True).start()
                        self._send(200, {"ok": True})
                    elif self.path == "/rec/lookup":
                        found = rec.lookup(str(data.get("url") or ""))
                        self._send(200, found or {"session": None})
                    else:
                        self._send(404, {"error": "not found"})
                except (ValueError, OSError, json.JSONDecodeError) as e:
                    self._send(400, {"error": str(e)})
                except Exception as e:  # noqa: BLE001  answer instead of dropping the line
                    try:
                        self._send(500, {"error": str(e) or e.__class__.__name__})
                    except OSError:
                        pass

        self.server = _Server(("127.0.0.1", port), Handler)
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
