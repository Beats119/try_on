"""Tests for phone_mouse.mouse — OS mouse injection backends.

Real OS libraries are injected as fakes so every backend's event translation
is verified without needing a display, Windows, or macOS.
"""
import unittest

from phone_mouse.mouse import (
    MacMouse,
    MouseUnavailable,
    NullMouse,
    WindowsMouse,
    XTestMouse,
    create_mouse,
)


class FakeXLib:
    """Stand-in for libX11/libXtst with the exact methods XTestMouse uses."""

    def __init__(self, display=True):
        self.calls = []
        self._display = object() if display else None

    def XOpenDisplay(self, name):
        self.calls.append(("open", name))
        return self._display

    def XCloseDisplay(self, d):
        self.calls.append(("close",))

    def XTestFakeMotionEvent(self, d, screen, x, y, delay):
        self.calls.append(("motion", x, y))

    def XTestFakeButtonEvent(self, d, button, press, delay):
        self.calls.append(("button", button, press))

    def XTestFakeKeyEvent(self, d, keycode, press, delay):
        self.calls.append(("key", keycode, press))

    def XFlush(self, d):
        self.calls.append(("flush",))

    def XQueryPointer(self, *args):
        return False

    def of_kind(self, kind):
        return [c for c in self.calls if c[0] == kind]


class TestNullMouse(unittest.TestCase):
    def test_records_events_and_tracks_position(self):
        m = NullMouse()
        m.move(10, -5)
        m.click("left", 2)
        m.scroll(0, 30)
        m.zoom(10)
        self.assertEqual(m.position(), (970.0, 535.0))
        self.assertEqual(
            m.events,
            [("move", 10, -5), ("click", "left", 2), ("scroll", 0, 30), ("zoom", 10)],
        )

    def test_position_clamped_nonnegative(self):
        m = NullMouse()
        m.move(-99999, -99999)
        self.assertEqual(m.position(), (0.0, 0.0))

    def test_button_down_up_recorded(self):
        m = NullMouse()
        m.button_down("left")
        m.button_up("left")
        self.assertEqual(m.events, [("button_down", "left"), ("button_up", "left")])

    def test_backend_name(self):
        self.assertIn("null", NullMouse().backend_name.lower())


class TestXTestMouse(unittest.TestCase):
    def test_move_uses_tracked_absolute_position(self):
        lib = FakeXLib()
        m = XTestMouse(lib=lib)
        m.move(10, 5)
        m.move(5, 5)
        self.assertEqual(lib.of_kind("motion"), [("motion", 10, 5), ("motion", 15, 10)])

    def test_left_click_is_button_1_press_then_release(self):
        lib = FakeXLib()
        m = XTestMouse(lib=lib)
        m.click("left", 1)
        self.assertEqual(lib.of_kind("button"), [("button", 1, 1), ("button", 1, 0)])

    def test_right_click_is_button_3(self):
        lib = FakeXLib()
        m = XTestMouse(lib=lib)
        m.click("right", 1)
        self.assertEqual(lib.of_kind("button"), [("button", 3, 1), ("button", 3, 0)])

    def test_double_click_repeats(self):
        lib = FakeXLib()
        m = XTestMouse(lib=lib)
        m.click("left", 2)
        self.assertEqual(len(lib.of_kind("button")), 4)

    def test_scroll_down_is_button_5(self):
        lib = FakeXLib()
        m = XTestMouse(lib=lib)
        m.scroll(0, 30)
        self.assertEqual(lib.of_kind("button"), [("button", 5, 1), ("button", 5, 0)])

    def test_scroll_up_is_button_4_and_ticks_scale(self):
        lib = FakeXLib()
        m = XTestMouse(lib=lib)
        m.scroll(0, -90)
        presses = [c for c in lib.of_kind("button") if c[2] == 1]
        self.assertEqual(len(presses), 3)
        self.assertTrue(all(c[1] == 4 for c in presses))

    def test_zoom_holds_ctrl_around_scroll(self):
        lib = FakeXLib()
        m = XTestMouse(lib=lib)
        m.zoom(30)
        self.assertEqual(lib.of_kind("key"), [("key", 37, 1), ("key", 37, 0)])
        self.assertEqual(lib.of_kind("button"), [("button", 5, 1), ("button", 5, 0)])

    def test_no_display_raises_unavailable(self):
        with self.assertRaises(MouseUnavailable):
            XTestMouse(lib=FakeXLib(display=False))


class TestWindowsMouse(unittest.TestCase):
    def _make(self):
        sent = []
        return WindowsMouse(send=lambda inputs: sent.extend(inputs)), sent

    def test_move_is_relative(self):
        m, sent = self._make()
        m.move(7, -3)
        self.assertEqual(sent, [{"type": "mouse", "flags": 0x0001, "dx": 7, "dy": -3, "data": 0}])

    def test_left_click_flags(self):
        m, sent = self._make()
        m.click("left", 1)
        self.assertEqual([i["flags"] for i in sent], [0x0002, 0x0004])

    def test_right_click_flags(self):
        m, sent = self._make()
        m.click("right", 1)
        self.assertEqual([i["flags"] for i in sent], [0x0008, 0x0010])

    def test_scroll_down_is_negative_wheel_data(self):
        m, sent = self._make()
        m.scroll(0, 30)
        self.assertEqual(sent, [{"type": "mouse", "flags": 0x0800, "dx": 0, "dy": 0, "data": -120}])

    def test_zoom_holds_ctrl_around_wheel(self):
        m, sent = self._make()
        m.zoom(30)
        self.assertEqual(sent[0], {"type": "key", "vk": 0x11, "down": True})
        self.assertEqual(sent[-1], {"type": "key", "vk": 0x11, "down": False})
        self.assertEqual(sent[1]["flags"], 0x0800)


class TestMacMouse(unittest.TestCase):
    def _make(self):
        posted = []
        return MacMouse(post=lambda ev: posted.append(ev)), posted

    def test_move_posts_moved_with_tracked_position(self):
        m, posted = self._make()
        m.move(10, 5)
        self.assertEqual(posted, [{"type": "move", "x": 10.0, "y": 5.0}])

    def test_click_posts_down_then_up(self):
        m, posted = self._make()
        m.click("left", 1)
        self.assertEqual([e["type"] for e in posted], ["down", "up"])
        self.assertEqual(posted[0]["button"], "left")

    def test_scroll_down_negates_for_quartz(self):
        # Our convention: dy > 0 means scroll down. Quartz: positive = up.
        m, posted = self._make()
        m.scroll(0, 30)
        self.assertEqual(posted, [{"type": "scroll", "wheel_dy": -30, "wheel_dx": 0}])

    def test_zoom_posts_zoom(self):
        m, posted = self._make()
        m.zoom(20)
        self.assertEqual(posted, [{"type": "zoom", "delta": 20}])


class TestCreateMouse(unittest.TestCase):
    def test_explicit_null(self):
        self.assertIsInstance(create_mouse("null"), NullMouse)

    def test_auto_without_display_falls_back_to_null(self):
        m = create_mouse(None, env={}, platform_name="Linux")
        self.assertIsInstance(m, NullMouse)
        self.assertIn("DISPLAY", m.note)

    def test_auto_unknown_platform_falls_back_to_null(self):
        m = create_mouse(None, env={}, platform_name="Plan9")
        self.assertIsInstance(m, NullMouse)

    def test_explicit_xtest_with_failing_loader_raises(self):
        def boom():
            raise MouseUnavailable("no libXtst")

        with self.assertRaises(MouseUnavailable):
            create_mouse("xtest", loaders={"xtest": boom})

    def test_explicit_unknown_name_raises_value_error(self):
        with self.assertRaises(ValueError):
            create_mouse("telepathy")


if __name__ == "__main__":
    unittest.main()
