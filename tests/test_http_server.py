"""Tests for phone_mouse.http_server — static serving, health, WS upgrade."""
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest

from phone_mouse.http_server import TouchpadHTTPServer, configure_socket
from phone_mouse.mouse import NullMouse


class TestHTTPServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        with open(os.path.join(root, "index.html"), "w") as f:
            f.write("<!doctype html><title>Phone Mouse</title><div id='touchpad'>x</div>")
        with open(os.path.join(root, "app.js"), "w") as f:
            f.write("// phone mouse client\n")
        with open(os.path.join(root, "style.css"), "w") as f:
            f.write("body { margin: 0; }\n")
        cls.mouse = NullMouse()
        cls.server = TouchpadHTTPServer("127.0.0.1", 0, "tok", cls.mouse, root)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, resp.getheader("Content-Type"), body

    def test_root_serves_index(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"touchpad", body)

    def test_app_js_content_type(self):
        status, ctype, body = self.get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        self.assertIn(b"phone mouse client", body)

    def test_style_css_content_type(self):
        status, ctype, _ = self.get("/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", ctype)

    def test_health_json(self):
        status, ctype, body = self.get("/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertIn("backend", data)

    def test_unknown_path_404(self):
        status, _, _ = self.get("/definitely-not-here")
        self.assertEqual(status, 404)

    def test_traversal_blocked(self):
        status, _, _ = self.get("/../secret.txt")
        self.assertEqual(status, 404)

    def test_ws_without_upgrade_is_426(self):
        status, _, _ = self.get("/ws")
        self.assertEqual(status, 426)


class TestConfigureSocket(unittest.TestCase):
    """WebSocket sockets must disable Nagle (TCP_NODELAY).

    Nagle batches small packets and can add 40-200ms per move on a
    touchpad link. This is the single biggest latency fix.
    """

    def test_tcp_nodelay_enabled(self):
        # Real TCP pair (AF_UNIX socketpairs don't support TCP_NODELAY).
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client = socket.create_connection(listener.getsockname(), timeout=5)
        server_side, _ = listener.accept()
        try:
            configure_socket(server_side)
            self.assertEqual(
                server_side.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY), 1
            )
        finally:
            client.close()
            server_side.close()
            listener.close()


if __name__ == "__main__":
    unittest.main()
