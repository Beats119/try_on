"""End-to-end tests: real HTTP server + real WebSocket wire protocol.

A minimal stdlib WebSocket client speaks to the server exactly like the
phone browser does, and we assert the laptop-side mouse saw the right events.
"""
import base64
import http.client
import os
import socket
import threading
import time
import unittest

from phone_mouse.http_server import TouchpadHTTPServer
from phone_mouse.mouse import NullMouse
from phone_mouse.protocol import decode_message, encode_message, make_message
from phone_mouse.websocket import FrameDecoder, encode_frame


class WSClient:
    """Tiny RFC 6455 client over a raw socket (what the phone browser does)."""

    def __init__(self, port, path="/ws"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.settimeout(5)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET %s HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, key)
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("server closed during handshake")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        assert b"101" in head.split(b"\r\n")[0], head
        self.decoder = FrameDecoder(expect_mask=False)
        self._leftover = rest
        self.closed = False

    def send(self, msg):
        frame = encode_frame(encode_message(msg), mask_key=os.urandom(4))
        self.sock.sendall(frame)

    def send_raw_text(self, text):
        self.sock.sendall(encode_frame(text.encode(), mask_key=os.urandom(4)))

    def recv_messages(self, timeout=3.0):
        """Collect decoded protocol messages until timeout; returns list."""
        self.sock.settimeout(timeout)
        out = []
        data = self._leftover
        self._leftover = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if data:
                for opcode, payload in self.decoder.feed(data):
                    if opcode == 1:
                        out.append(decode_message(payload))
                    elif opcode == 8:
                        self.closed = True
                        return out
                    elif opcode == 9:  # ping -> pong
                        self.sock.sendall(encode_frame(payload, opcode=10))
                data = b""
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                self.closed = True
                break
            data = chunk
        return out

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
        cls.mouse = NullMouse()
        cls.server = TouchpadHTTPServer("127.0.0.1", 0, "test-token", cls.mouse, static_dir)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.mouse.events.clear()

    def test_page_served(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn(b"touchpad", body.lower())

    def test_hello_then_tap_clicks(self):
        c = WSClient(self.port)
        try:
            c.send(make_message("hello", {"token": "test-token", "name": "test", "version": 1}, seq=1, ts=0.0))
            msgs = c.recv_messages()
            self.assertTrue(any(m["t"] == "ack" for m in msgs))

            c.send(make_message("touch_start", {"points": [{"id": 1, "x": 100, "y": 100}]}, seq=2, ts=0.0))
            c.send(make_message("touch_end", {"points": [{"id": 1, "x": 100, "y": 100}]}, seq=3, ts=0.1))
            c.recv_messages(timeout=1.0)
            self.assertIn(("click", "left", 1), self.mouse.events)
        finally:
            c.close()

    def test_url_token_skips_hello(self):
        c = WSClient(self.port, path="/ws?token=test-token")
        try:
            c.send(make_message("touch_start", {"points": [{"id": 1, "x": 0, "y": 0}]}, seq=1, ts=0.0))
            c.send(make_message("touch_end", {"points": [{"id": 1, "x": 0, "y": 0}]}, seq=2, ts=0.1))
            c.recv_messages(timeout=1.0)
            self.assertIn(("click", "left", 1), self.mouse.events)
        finally:
            c.close()

    def test_wrong_token_rejected_and_closed(self):
        c = WSClient(self.port)
        try:
            c.send(make_message("hello", {"token": "nope", "name": "x", "version": 1}, seq=1, ts=0.0))
            msgs = c.recv_messages(timeout=3.0)
            self.assertTrue(any(m["t"] == "error" for m in msgs))
            self.assertTrue(c.closed)
        finally:
            c.close()

    def test_malformed_message_closes_connection(self):
        c = WSClient(self.port)
        try:
            c.send_raw_text("this is not json")
            msgs = c.recv_messages(timeout=3.0)
            self.assertTrue(any(m["t"] == "error" for m in msgs))
            self.assertTrue(c.closed)
        finally:
            c.close()

    def test_two_finger_scroll_reaches_mouse(self):
        c = WSClient(self.port, path="/ws?token=test-token")
        try:
            c.send(make_message("touch_start", {"points": [{"id": 1, "x": 100, "y": 100}]}, seq=1, ts=0.0))
            c.send(make_message("touch_start", {"points": [{"id": 2, "x": 200, "y": 100}]}, seq=2, ts=0.0))
            c.send(
                make_message(
                    "touch_move",
                    {"points": [{"id": 1, "x": 100, "y": 140}, {"id": 2, "x": 200, "y": 140}]},
                    seq=3,
                    ts=0.1,
                )
            )
            c.recv_messages(timeout=1.0)
            scrolls = [e for e in self.mouse.events if e[0] == "scroll"]
            self.assertTrue(len(scrolls) > 0)
            self.assertGreater(scrolls[0][2], 0)
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
