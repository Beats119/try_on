"""Tests for phone_mouse.gestures — the touch -> mouse gesture engine.

The engine is a pure function of (event, time): tests drive it with fake
timestamps so every behaviour is deterministic.
"""
import unittest

from phone_mouse.gestures import GestureEngine


def kinds(events):
    return [e.kind for e in events]


class TestSingleFingerMove(unittest.TestCase):
    def test_move_is_relative(self):
        g = GestureEngine()
        self.assertEqual(g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=0.0), [])
        evs = g.handle("touch_move", [{"id": 1, "x": 110, "y": 105}], now=0.1)
        self.assertEqual(kinds(evs), ["move"])
        # dist = sqrt(125) ~= 11.18px in 0.1s -> speed ~= 111.8 px/s
        # accel factor = 1 + 111.8/500 ~= 1.2236
        self.assertAlmostEqual(evs[0].dx, 10 * 1.2236, places=1)
        self.assertAlmostEqual(evs[0].dy, 5 * 1.2236, places=1)

    def test_sensitivity_scales_movement(self):
        g = GestureEngine(sensitivity=2.0)
        g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.0)
        evs = g.handle("touch_move", [{"id": 1, "x": 50, "y": 0}], now=0.1)
        # speed = 500 px/s -> factor 2.0; dx = 50 * 2.0 (sens) * 2.0 (accel) = 200
        self.assertAlmostEqual(evs[0].dx, 200.0, places=0)

    def test_acceleration_fast_flick_moves_further(self):
        slow = GestureEngine()
        slow.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.0)
        slow_evs = slow.handle("touch_move", [{"id": 1, "x": 10, "y": 0}], now=0.1)

        fast = GestureEngine()
        fast.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.0)
        fast_evs = fast.handle("touch_move", [{"id": 1, "x": 10, "y": 0}], now=0.01)

        self.assertGreater(fast_evs[0].dx, slow_evs[0].dx)

    def test_accel_can_be_disabled(self):
        g = GestureEngine(accel=False)
        g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.0)
        evs = g.handle("touch_move", [{"id": 1, "x": 10, "y": 0}], now=0.01)
        self.assertAlmostEqual(evs[0].dx, 10.0, places=6)

    def test_move_without_start_is_ignored(self):
        g = GestureEngine()
        self.assertEqual(g.handle("touch_move", [{"id": 9, "x": 50, "y": 50}], now=0.0), [])

    def test_end_of_unknown_id_is_ignored(self):
        g = GestureEngine()
        self.assertEqual(g.handle("touch_end", [{"id": 9, "x": 0, "y": 0}], now=0.0), [])

    def test_finger_id_reuse_after_end(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.0)
        g.handle("touch_end", [{"id": 1, "x": 0, "y": 0}], now=1.0)  # slow: no click
        g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=2.0)
        evs = g.handle("touch_move", [{"id": 1, "x": 110, "y": 100}], now=2.1)
        self.assertEqual(kinds(evs), ["move"])


class TestTap(unittest.TestCase):
    def test_quick_tap_clicks_left(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=0.0)
        evs = g.handle("touch_end", [{"id": 1, "x": 100, "y": 100}], now=0.1)
        self.assertEqual(kinds(evs), ["click"])
        self.assertEqual(evs[0].button, "left")
        self.assertEqual(evs[0].count, 1)

    def test_slow_press_does_not_click(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=0.0)
        evs = g.handle("touch_end", [{"id": 1, "x": 100, "y": 100}], now=0.5)
        self.assertEqual(evs, [])

    def test_moved_press_does_not_click(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=0.0)
        g.handle("touch_move", [{"id": 1, "x": 140, "y": 100}], now=0.05)
        evs = g.handle("touch_end", [{"id": 1, "x": 140, "y": 100}], now=0.1)
        self.assertEqual(evs, [])

    def test_double_tap(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.0)
        first = g.handle("touch_end", [{"id": 1, "x": 0, "y": 0}], now=0.1)
        g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.3)
        second = g.handle("touch_end", [{"id": 1, "x": 0, "y": 0}], now=0.4)
        self.assertEqual(kinds(first), ["click"])
        self.assertEqual(kinds(second), ["double_click"])
        self.assertEqual(second[0].count, 2)

    def test_third_tap_after_double_is_single_again(self):
        g = GestureEngine()
        for start, end in [(0.0, 0.1), (0.3, 0.4), (0.6, 0.7)]:
            g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=start)
            evs = g.handle("touch_end", [{"id": 1, "x": 0, "y": 0}], now=end)
        self.assertEqual(kinds(evs), ["click"])

    def test_taps_far_apart_are_singles(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.0)
        g.handle("touch_end", [{"id": 1, "x": 0, "y": 0}], now=0.1)
        g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=2.0)
        evs = g.handle("touch_end", [{"id": 1, "x": 0, "y": 0}], now=2.1)
        self.assertEqual(kinds(evs), ["click"])


class TestLongPressDrag(unittest.TestCase):
    def test_long_press_starts_drag_and_release_ends_it(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=0.0)
        down = g.tick(now=0.6)
        self.assertEqual(kinds(down), ["button_down"])
        move = g.handle("touch_move", [{"id": 1, "x": 120, "y": 100}], now=0.7)
        self.assertEqual(kinds(move), ["move"])
        # Dragging uses raw sensitivity (no acceleration) for precision.
        self.assertAlmostEqual(move[0].dx, 20.0, places=6)
        up = g.handle("touch_end", [{"id": 1, "x": 120, "y": 100}], now=0.8)
        self.assertEqual(kinds(up), ["button_up"])

    def test_long_press_after_moving_far_does_not_drag(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.0)
        g.handle("touch_move", [{"id": 1, "x": 50, "y": 0}], now=0.1)
        self.assertEqual(g.tick(now=0.6), [])

    def test_tick_with_no_touch_is_empty(self):
        self.assertEqual(GestureEngine().tick(now=10.0), [])

    def test_no_click_after_drag(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.0)
        g.tick(now=0.6)
        evs = g.handle("touch_end", [{"id": 1, "x": 0, "y": 0}], now=0.7)
        self.assertEqual(kinds(evs), ["button_up"])


class TestTwoFinger(unittest.TestCase):
    def _two_finger_start(self, g):
        g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=0.0)
        g.handle("touch_start", [{"id": 2, "x": 200, "y": 100}], now=0.02)

    def test_two_finger_tap_right_clicks(self):
        g = GestureEngine()
        self._two_finger_start(g)
        self.assertEqual(g.handle("touch_end", [{"id": 1, "x": 100, "y": 100}], now=0.1), [])
        evs = g.handle("touch_end", [{"id": 2, "x": 200, "y": 100}], now=0.12)
        self.assertEqual(kinds(evs), ["right_click"])
        self.assertEqual(evs[0].button, "right")

    def test_two_finger_move_scrolls(self):
        g = GestureEngine()
        self._two_finger_start(g)
        evs = g.handle(
            "touch_move",
            [{"id": 1, "x": 100, "y": 120}, {"id": 2, "x": 200, "y": 120}],
            now=0.1,
        )
        self.assertEqual(kinds(evs), ["scroll"])
        self.assertAlmostEqual(evs[0].dy, 20.0, places=6)
        self.assertAlmostEqual(evs[0].dx, 0.0, places=6)

    def test_pinch_out_zooms_in(self):
        g = GestureEngine()
        self._two_finger_start(g)
        evs = g.handle(
            "touch_move",
            [{"id": 1, "x": 90, "y": 100}, {"id": 2, "x": 210, "y": 100}],
            now=0.1,
        )
        self.assertIn("zoom", kinds(evs))
        zoom = [e for e in evs if e.kind == "zoom"][0]
        self.assertAlmostEqual(zoom.dx, 20.0, places=6)

    def test_pinch_in_zooms_out(self):
        g = GestureEngine()
        self._two_finger_start(g)
        evs = g.handle(
            "touch_move",
            [{"id": 1, "x": 110, "y": 100}, {"id": 2, "x": 190, "y": 100}],
            now=0.1,
        )
        zoom = [e for e in evs if e.kind == "zoom"][0]
        self.assertAlmostEqual(zoom.dx, -20.0, places=6)

    def test_second_finger_cancels_pending_tap(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 0, "y": 0}], now=0.0)
        g.handle("touch_start", [{"id": 2, "x": 50, "y": 0}], now=0.05)
        # Lift finger 1 quickly, then move finger 2 a lot and lift: no click at all.
        g.handle("touch_end", [{"id": 1, "x": 0, "y": 0}], now=0.1)
        g.handle("touch_move", [{"id": 2, "x": 150, "y": 0}], now=0.15)
        evs = g.handle("touch_end", [{"id": 2, "x": 150, "y": 0}], now=0.2)
        self.assertNotIn("click", kinds(evs))
        self.assertNotIn("right_click", kinds(evs))

    def test_remaining_finger_moves_cursor_after_lift(self):
        g = GestureEngine()
        self._two_finger_start(g)
        g.handle("touch_end", [{"id": 1, "x": 100, "y": 100}], now=0.3)
        evs = g.handle("touch_move", [{"id": 2, "x": 220, "y": 100}], now=0.4)
        self.assertEqual(kinds(evs), ["move"])
        self.assertGreater(evs[0].dx, 0)

    def test_third_finger_is_ignored(self):
        g = GestureEngine()
        self._two_finger_start(g)
        g.handle("touch_start", [{"id": 3, "x": 300, "y": 300}], now=0.05)
        evs = g.handle("touch_move", [{"id": 3, "x": 400, "y": 400}], now=0.1)
        self.assertEqual(evs, [])
        # Ending the ignored finger must not disturb the other two.
        self.assertEqual(g.handle("touch_end", [{"id": 3, "x": 400, "y": 400}], now=0.15), [])


class TestScrollInertia(unittest.TestCase):
    def test_fast_scroll_release_has_momentum(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=0.0)
        g.handle("touch_start", [{"id": 2, "x": 200, "y": 100}], now=0.0)
        # Fast downward scroll: 40px in 0.05s -> 800 px/s.
        g.handle("touch_move", [{"id": 1, "x": 100, "y": 140}, {"id": 2, "x": 200, "y": 140}], now=0.05)
        g.handle("touch_end", [{"id": 1, "x": 100, "y": 140}], now=0.06)
        g.handle("touch_end", [{"id": 2, "x": 200, "y": 140}], now=0.06)

        first = g.tick(now=0.08)
        self.assertEqual(kinds(first), ["scroll"])
        self.assertGreater(first[0].dy, 0)
        second = g.tick(now=0.10)
        self.assertEqual(kinds(second), ["scroll"])
        self.assertLess(second[0].dy, first[0].dy)  # decaying

        # Eventually inertia dies out completely.
        t = 0.10
        for _ in range(200):
            t += 0.05
            if g.tick(now=t) == []:
                break
        else:
            self.fail("inertia never stopped")

    def test_slow_scroll_release_has_no_inertia(self):
        g = GestureEngine()
        g.handle("touch_start", [{"id": 1, "x": 100, "y": 100}], now=0.0)
        g.handle("touch_start", [{"id": 2, "x": 200, "y": 100}], now=0.0)
        g.handle("touch_move", [{"id": 1, "x": 100, "y": 105}, {"id": 2, "x": 200, "y": 105}], now=0.5)
        g.handle("touch_end", [{"id": 1, "x": 100, "y": 105}], now=0.55)
        g.handle("touch_end", [{"id": 2, "x": 200, "y": 105}], now=0.55)
        self.assertEqual(g.tick(now=0.6), [])


if __name__ == "__main__":
    unittest.main()
