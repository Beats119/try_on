"""Tests for two-finger scroll/pinch disambiguation (mode locking).

Bug: scrolling with two fingers also triggered zoom (and vice versa)
because both centroid movement and finger-distance change fire together.
Fix: the gesture that clearly wins first locks in for the whole
two-finger session; the other is suppressed until fingers lift.
"""
import unittest

from phone_mouse.gestures import GestureEngine


def kinds(events):
    return [e.kind for e in events]


class TestScrollPinchDisambiguation(unittest.TestCase):
    def _start(self, g):
        g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=0.0)
        g.handle("touch_start", [{"id": 2, "x": 200, "y": 100}], now=0.0)

    def test_scroll_with_slight_finger_drift_does_not_zoom(self):
        g = GestureEngine()
        self._start(g)
        # Scroll down 40px while fingers drift 4px apart (natural jitter).
        evs = g.handle(
            "touch_move",
            [{"id": 1, "x": 98, "y": 140}, {"id": 2, "x": 202, "y": 140}],
            now=0.1,
        )
        self.assertIn("scroll", kinds(evs))
        self.assertNotIn("zoom", kinds(evs))

    def test_pinch_with_slight_centroid_drift_does_not_scroll(self):
        g = GestureEngine()
        self._start(g)
        # Pinch out 30px while the pair drifts 3px sideways.
        evs = g.handle(
            "touch_move",
            [{"id": 1, "x": 85, "y": 103}, {"id": 2, "x": 215, "y": 103}],
            now=0.1,
        )
        self.assertIn("zoom", kinds(evs))
        self.assertNotIn("scroll", kinds(evs))

    def test_scroll_mode_locks_for_the_session(self):
        g = GestureEngine()
        self._start(g)
        # Lock into scroll first...
        g.handle(
            "touch_move",
            [{"id": 1, "x": 100, "y": 140}, {"id": 2, "x": 200, "y": 140}],
            now=0.1,
        )
        # ...then pinch hard: still scroll only, no zoom.
        evs = g.handle(
            "touch_move",
            [{"id": 1, "x": 60, "y": 140}, {"id": 2, "x": 240, "y": 140}],
            now=0.2,
        )
        self.assertNotIn("zoom", kinds(evs))

    def test_pinch_mode_locks_for_the_session(self):
        g = GestureEngine()
        self._start(g)
        g.handle(
            "touch_move",
            [{"id": 1, "x": 80, "y": 100}, {"id": 2, "x": 220, "y": 100}],
            now=0.1,
        )
        # Then drag both fingers down hard: still zoom only, no scroll.
        evs = g.handle(
            "touch_move",
            [{"id": 1, "x": 80, "y": 160}, {"id": 2, "x": 220, "y": 160}],
            now=0.2,
        )
        self.assertNotIn("scroll", kinds(evs))

    def test_tiny_ambiguous_movement_emits_nothing(self):
        g = GestureEngine()
        self._start(g)
        evs = g.handle(
            "touch_move",
            [{"id": 1, "x": 101, "y": 102}, {"id": 2, "x": 201, "y": 102}],
            now=0.1,
        )
        self.assertEqual(evs, [])

    def test_new_session_can_choose_the_other_mode(self):
        g = GestureEngine()
        self._start(g)
        g.handle(
            "touch_move",
            [{"id": 1, "x": 100, "y": 140}, {"id": 2, "x": 200, "y": 140}],
            now=0.1,
        )  # scroll session
        g.handle("touch_end", [{"id": 1, "x": 100, "y": 140}], now=0.2)
        g.handle("touch_end", [{"id": 2, "x": 200, "y": 140}], now=0.2)
        # Fresh two-finger session: pinch must work again.
        g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=1.0)
        g.handle("touch_start", [{"id": 2, "x": 200, "y": 100}], now=1.0)
        evs = g.handle(
            "touch_move",
            [{"id": 1, "x": 80, "y": 100}, {"id": 2, "x": 220, "y": 100}],
            now=1.1,
        )
        self.assertIn("zoom", kinds(evs))
        self.assertNotIn("scroll", kinds(evs))

    def test_lifting_one_finger_resets_mode(self):
        g = GestureEngine()
        self._start(g)
        g.handle(
            "touch_move",
            [{"id": 1, "x": 100, "y": 140}, {"id": 2, "x": 200, "y": 140}],
            now=0.1,
        )  # scroll lock
        g.handle("touch_end", [{"id": 1, "x": 100, "y": 140}], now=0.2)
        # Second finger lands again -> fresh session, pinch allowed.
        g.handle("touch_start", [{"id": 3, "x": 300, "y": 140}], now=0.3)
        evs = g.handle(
            "touch_move",
            [{"id": 2, "x": 180, "y": 140}, {"id": 3, "x": 320, "y": 140}],
            now=0.4,
        )
        self.assertIn("zoom", kinds(evs))


if __name__ == "__main__":
    unittest.main()
