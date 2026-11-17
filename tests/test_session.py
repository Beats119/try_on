"""Tests for phone_mouse.session — auth, sequencing, heartbeat, event flow."""
import unittest

from phone_mouse.mouse import NullMouse
from phone_mouse.protocol import make_message
from phone_mouse.session import Session


def hello(token="secret", seq=1, ts=0.0):
    return make_message("hello", {"token": token, "name": "phone", "version": 1}, seq=seq, ts=ts)


def touch(kind, points, seq, ts):
    return make_message(kind, {"points": points}, seq=seq, ts=ts)


class TestSessionAuth(unittest.TestCase):
    def test_wrong_token_closes_with_error(self):
        s = Session("secret", NullMouse())
        out = s.handle_message(hello("wrong"), now=0.0)
        self.assertTrue(s.closed)
        self.assertEqual(out[0]["t"], "error")
        self.assertEqual(out[0]["data"]["code"], "auth_failed")

    def test_touch_before_hello_closes(self):
        s = Session("secret", NullMouse())
        out = s.handle_message(touch("touch_start", [{"id": 1, "x": 0, "y": 0}], 1, 0.0), now=0.0)
        self.assertTrue(s.closed)
        self.assertEqual(out[0]["t"], "error")

    def test_correct_token_acks(self):
        s = Session("secret", NullMouse())
        out = s.handle_message(hello(), now=0.0)
        self.assertFalse(s.closed)
        self.assertEqual(out[0]["t"], "ack")
        self.assertTrue(out[0]["data"]["ok"])
        self.assertIn("backend", out[0]["data"])

    def test_closed_session_ignores_everything(self):
        s = Session("secret", NullMouse())
        s.handle_message(hello("wrong"), now=0.0)
        self.assertEqual(s.handle_message(hello(), now=1.0), [])
        self.assertEqual(s.tick(now=2.0), [])

    def test_preauthed_session_accepts_touch_without_hello(self):
        s = Session("secret", NullMouse(), preauthed=True)
        out = s.handle_message(touch("touch_start", [{"id": 1, "x": 0, "y": 0}], 1, 0.0), now=0.0)
        self.assertFalse(s.closed)
        self.assertEqual(out, [])


class TestSessionTouchFlow(unittest.TestCase):
    def setUp(self):
        self.mouse = NullMouse()
        self.s = Session("secret", self.mouse)
        self.s.handle_message(hello(), now=0.0)

    def test_tap_reaches_mouse_as_click(self):
        self.s.handle_message(touch("touch_start", [{"id": 1, "x": 100, "y": 100}], 2, 0.0), now=0.0)
        self.s.handle_message(touch("touch_end", [{"id": 1, "x": 100, "y": 100}], 3, 0.1), now=0.1)
        self.assertIn(("click", "left", 1), self.mouse.events)

    def test_move_reaches_mouse_and_demo_state_reports_cursor(self):
        self.s.handle_message(touch("touch_start", [{"id": 1, "x": 0, "y": 0}], 2, 0.0), now=0.0)
        out = self.s.handle_message(touch("touch_move", [{"id": 1, "x": 50, "y": 0}], 3, 0.1), now=0.1)
        self.assertTrue(any(e[0] == "move" for e in self.mouse.events))
        states = [m for m in out if m["t"] == "state"]
        self.assertEqual(len(states), 1)
        self.assertGreater(states[0]["data"]["x"], 960.0)  # moved right from start

    def test_duplicate_seq_dropped(self):
        self.s.handle_message(touch("touch_start", [{"id": 1, "x": 0, "y": 0}], 2, 0.0), now=0.0)
        self.s.handle_message(touch("touch_move", [{"id": 1, "x": 50, "y": 0}], 3, 0.1), now=0.1)
        moves_after_first = len([e for e in self.mouse.events if e[0] == "move"])
        # Same seq again (retransmit / out-of-order): must be dropped.
        self.s.handle_message(touch("touch_move", [{"id": 1, "x": 100, "y": 0}], 3, 0.2), now=0.2)
        moves_after_dup = len([e for e in self.mouse.events if e[0] == "move"])
        self.assertEqual(moves_after_first, moves_after_dup)
        # Next seq works again.
        self.s.handle_message(touch("touch_move", [{"id": 1, "x": 100, "y": 0}], 4, 0.3), now=0.3)
        self.assertGreater(len([e for e in self.mouse.events if e[0] == "move"]), moves_after_dup)

    def test_ping_replies_pong_with_same_ts(self):
        out = self.s.handle_message(make_message("ping", {"ts": 42.5}, seq=9, ts=42.5), now=1.0)
        self.assertEqual(out[0]["t"], "pong")
        self.assertEqual(out[0]["data"]["ts"], 42.5)

    def test_bye_closes(self):
        self.s.handle_message(make_message("bye", {}, seq=9, ts=1.0), now=1.0)
        self.assertTrue(self.s.closed)


class TestSessionHeartbeat(unittest.TestCase):
    def setUp(self):
        self.s = Session("secret", NullMouse(), heartbeat_interval=5.0, heartbeat_timeout=15.0)
        self.s.handle_message(hello(), now=0.0)

    def test_tick_sends_ping_after_interval(self):
        self.assertEqual(self.s.tick(now=1.0), [])
        out = self.s.tick(now=6.0)
        self.assertEqual([m["t"] for m in out], ["ping"])

    def test_timeout_closes_silent_connection(self):
        out = self.s.tick(now=16.0)
        self.assertTrue(self.s.closed)
        self.assertEqual(out[0]["t"], "error")
        self.assertEqual(out[0]["data"]["code"], "timeout")

    def test_activity_resets_timeout(self):
        self.s.handle_message(make_message("pong", {"ts": 10.0}, seq=5, ts=10.0), now=10.0)
        self.assertFalse(self.s.closed)
        self.s.tick(now=20.0)  # 10s after last activity: still alive
        self.assertFalse(self.s.closed)
        self.s.tick(now=26.0)  # 16s after last activity: dead
        self.assertTrue(self.s.closed)

    def test_long_press_drag_via_tick(self):
        mouse = NullMouse()
        s = Session("secret", mouse)
        s.handle_message(hello(), now=0.0)
        s.handle_message(touch("touch_start", [{"id": 1, "x": 0, "y": 0}], 2, 0.0), now=0.0)
        s.tick(now=0.6)
        self.assertIn(("button_down", "left"), mouse.events)


if __name__ == "__main__":
    unittest.main()
