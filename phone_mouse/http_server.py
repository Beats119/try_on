"""HTTP + WebSocket server: serves the touchpad page and the phone link.

One port does everything:

- ``GET /`` (+ ``/app.js``, ``/style.css``) — the touchpad web page
- ``GET /health`` — JSON status (backend, connected clients)
- ``GET /ws`` with ``Upgrade: websocket`` — the real-time phone link

The WebSocket loop runs on the connection's own thread with a short socket
timeout so :meth:`Session.tick` fires regularly (heartbeat pings, long-press
drag, scroll inertia, dead-connection cleanup). Only one phone controls the
laptop at a time: a new connection *takes over* from a stale one instead of
fighting it — reconnects after a Wi-Fi blip just work.

``?token=...`` on the WebSocket URL pre-authenticates the session, so the
phone is ready the instant the page loads. Without it, the client must send
a ``hello`` message with the token first.
"""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .gestures import GestureEngine
from .protocol import ProtocolError, decode_message, encode_message
from .session import Session
from .websocket import (
    OP_CLOSE,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    FrameDecoder,
    WSHandshakeError,
    WSProtocolError,
    build_handshake_response,
    encode_frame,
)

_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def lan_ip():
    """Best-effort LAN IPv4 address (no traffic is actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def configure_socket(conn):
    """Tune a WebSocket socket for touchpad latency. Never raises.

    - TCP_NODELAY: disable Nagle. Nagle batches small packets and can add
      40-200ms to every move — the #1 cause of "laggy touchpad" reports.
    - TCP keepalive: notice dead phones quickly (belt-and-braces with the
      application-level heartbeat).
    """
    try:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except (OSError, AttributeError):
        pass
    try:
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except (OSError, AttributeError):
        pass


class TouchpadHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host, port, token, mouse, static_dir, gesture_kwargs=None):
        self.token = token
        self.mouse = mouse
        self.static_dir = static_dir
        self.gesture_kwargs = gesture_kwargs or {}
        self._ws_lock = threading.Lock()
        self._active_ws = None  # socket of the controlling phone, if any
        super().__init__((host, port), _Handler)

    # -- WebSocket takeover bookkeeping ------------------------------------

    def claim_ws(self, conn):
        """Register *conn* as the controller, evicting any previous one."""
        with self._ws_lock:
            old = self._active_ws
            self._active_ws = conn
        if old is not None and old is not conn:
            try:
                old.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                old.close()
            except OSError:
                pass

    def release_ws(self, conn):
        with self._ws_lock:
            if self._active_ws is conn:
                self._active_ws = None

    def client_count(self):
        with self._ws_lock:
            return 1 if self._active_ws is not None else 0


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PhoneMouse/1.0"

    def log_message(self, *args):  # keep the console clean
        pass

    # -- HTTP ---------------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in _STATIC_FILES:
            self._serve_static(*_STATIC_FILES[path])
        elif path == "/health":
            self._serve_json(
                {
                    "ok": True,
                    "backend": self.server.mouse.backend_name,
                    "clients": self.server.client_count(),
                }
            )
        elif path == "/ws":
            if "websocket" not in (self.headers.get("Upgrade") or "").lower():
                self._serve_text(426, "Upgrade Required: websocket")
            else:
                self._handle_ws(parsed)
        else:
            self._serve_text(404, "Not Found")

    def _serve_static(self, filename, content_type):
        import os

        full = os.path.join(self.server.static_dir, filename)
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            self._serve_text(404, "Not Found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_text(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- WebSocket ------------------------------------------------------------

    def _handle_ws(self, parsed):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self._serve_text(400, "Missing Sec-WebSocket-Key")
            return
        query = parse_qs(parsed.query)
        url_token = (query.get("token") or [None])[0]
        preauthed = url_token is not None and url_token == self.server.token

        conn = self.connection
        configure_socket(conn)
        self.server.claim_ws(conn)
        try:
            conn.sendall(build_handshake_response(key))
            session = Session(
                self.server.token,
                self.server.mouse,
                gesture=GestureEngine(**self.server.gesture_kwargs),
                preauthed=preauthed,
            )
            self._ws_loop(conn, session)
        except (OSError, WSHandshakeError, WSProtocolError):
            pass
        finally:
            self.server.release_ws(conn)
            try:
                conn.close()
            except OSError:
                pass
        self.close_connection = True

    def _ws_loop(self, conn, session):
        conn.settimeout(0.5)
        decoder = FrameDecoder(expect_mask=True)
        if session.authed:
            self._send_all(conn, session.welcome_messages())
        while not session.closed:
            try:
                data = conn.recv(65536)
            except socket.timeout:
                self._send_all(conn, session.tick(time.monotonic()))
                continue
            except OSError:
                break
            if not data:
                break
            try:
                frames = decoder.feed(data)
            except WSProtocolError:
                self._send_all(conn, [session.error_message("ws_protocol", "invalid frame")])
                break
            for opcode, payload in frames:
                if opcode == OP_CLOSE:
                    return
                if opcode == OP_PING:
                    conn.sendall(encode_frame(payload, opcode=OP_PONG))
                    continue
                if opcode != OP_TEXT:
                    continue
                try:
                    msg = decode_message(payload)
                except ProtocolError as exc:
                    self._send_all(conn, [session.error_message("bad_message", str(exc))])
                    session.closed = True
                    break
                self._send_all(conn, session.handle_message(msg, now=time.monotonic()))
                if session.closed:
                    break

    @staticmethod
    def _send_all(conn, messages):
        for msg in messages:
            try:
                conn.sendall(encode_frame(encode_message(msg), opcode=OP_TEXT))
            except (OSError, ProtocolError):
                return
