"""OS mouse injection: turn interpreted gestures into real cursor events.

Backends (auto-detected by :func:`create_mouse`):

- **Linux X11** — XTest via ctypes (``libXtst``/``libX11``). Direct X
  calls, no subprocess per event, so latency stays at touchpad levels.
  Wayland has no global input injection without a portal; if ``DISPLAY``
  is unset we fall back to the demo backend with an explanation.
- **Windows** — ``SendInput`` via ctypes (relative moves, wheel, ctrl+wheel
  zoom).
- **macOS** — Quartz ``CGEvent`` via ctypes.
- **Null (demo)** — records events and tracks a virtual cursor. Used in
  tests, headless environments, and the browser preview: the server echoes
  the virtual cursor position back to the page so you can *see* it move.

Scroll convention everywhere: ``dy > 0`` means scroll **down** (content
moves down), matching the gesture engine. Each backend translates this to
its native sign convention.

All OS libraries are loaded lazily and can be injected as fakes, so the
event translation of every backend is unit-testable on any machine.
"""
import ctypes
import os
import platform


class MouseUnavailable(Exception):
    """Raised when a requested OS backend cannot be initialised."""


class MouseBackend:
    backend_name = "base"

    def move(self, dx, dy):
        raise NotImplementedError

    def click(self, button="left", count=1):
        raise NotImplementedError

    def button_down(self, button="left"):
        raise NotImplementedError

    def button_up(self, button="left"):
        raise NotImplementedError

    def scroll(self, dx, dy):
        raise NotImplementedError

    def zoom(self, delta):
        raise NotImplementedError

    def position(self):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Demo / test backend
# ---------------------------------------------------------------------------


class NullMouse(MouseBackend):
    """Records every event; tracks a virtual cursor. Never touches the OS."""

    backend_name = "null (demo)"

    def __init__(self, start=(960.0, 540.0)):
        self.events = []
        self._x, self._y = float(start[0]), float(start[1])
        self.note = ""

    def move(self, dx, dy):
        self.events.append(("move", dx, dy))
        self._x = max(0.0, self._x + dx)
        self._y = max(0.0, self._y + dy)

    def click(self, button="left", count=1):
        self.events.append(("click", button, count))

    def button_down(self, button="left"):
        self.events.append(("button_down", button))

    def button_up(self, button="left"):
        self.events.append(("button_up", button))

    def scroll(self, dx, dy):
        self.events.append(("scroll", dx, dy))

    def zoom(self, delta):
        self.events.append(("zoom", delta))

    def position(self):
        return (self._x, self._y)


# ---------------------------------------------------------------------------
# Linux X11 via XTest
# ---------------------------------------------------------------------------

_XTEST_BUTTONS = {"left": 1, "middle": 2, "right": 3}
_XTEST_SCROLL_DOWN = 5
_XTEST_SCROLL_UP = 4
_XTEST_SCROLL_RIGHT = 7
_XTEST_SCROLL_LEFT = 6
_XKEY_CONTROL_L = 37
_PX_PER_WHEEL_TICK = 30.0


def _load_xtest_libs():
    try:
        xtst = ctypes.CDLL("libXtst.so.6")
        x11 = ctypes.CDLL("libX11.so.6")
    except OSError as exc:
        raise MouseUnavailable("libXtst/libX11 not found: %s" % exc) from exc
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
    xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_bool, ctypes.c_ulong]
    xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_bool, ctypes.c_ulong]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    return x11, xtst


class XTestMouse(MouseBackend):
    """Linux X11 mouse via XTestFake* events. Pass *lib* to inject a fake."""

    backend_name = "xtest (Linux X11)"

    def __init__(self, lib=None, display_name=None):
        if lib is None:
            x11, xtst = _load_xtest_libs()
            lib = _XTestLibAdapter(x11, xtst)
        self._lib = lib
        self._display = lib.XOpenDisplay(display_name)
        if not self._display:
            raise MouseUnavailable("no X display (DISPLAY=%r)" % (display_name,))
        self._x, self._y = 0.0, 0.0
        try:
            if lib.XQueryPointer(self._display, 0, 0, 0, 0, 0, 0, 0, 0):
                self._x, self._y = float(lib._qx), float(lib._qy)
        except Exception:
            pass

    def _flush(self):
        self._lib.XFlush(self._display)

    def move(self, dx, dy):
        self._x = max(0.0, self._x + dx)
        self._y = max(0.0, self._y + dy)
        self._lib.XTestFakeMotionEvent(self._display, 0, int(self._x), int(self._y), 0)
        self._flush()

    def click(self, button="left", count=1):
        code = _XTEST_BUTTONS[button]
        for _ in range(count):
            self._lib.XTestFakeButtonEvent(self._display, code, True, 0)
            self._lib.XTestFakeButtonEvent(self._display, code, False, 0)
        self._flush()

    def button_down(self, button="left"):
        self._lib.XTestFakeButtonEvent(self._display, _XTEST_BUTTONS[button], True, 0)
        self._flush()

    def button_up(self, button="left"):
        self._lib.XTestFakeButtonEvent(self._display, _XTEST_BUTTONS[button], False, 0)
        self._flush()

    def _wheel(self, button, amount_px):
        ticks = max(1, round(abs(amount_px) / _PX_PER_WHEEL_TICK))
        for _ in range(ticks):
            self._lib.XTestFakeButtonEvent(self._display, button, True, 0)
            self._lib.XTestFakeButtonEvent(self._display, button, False, 0)

    def scroll(self, dx, dy):
        if dy:
            self._wheel(_XTEST_SCROLL_DOWN if dy > 0 else _XTEST_SCROLL_UP, dy)
        if dx:
            self._wheel(_XTEST_SCROLL_RIGHT if dx > 0 else _XTEST_SCROLL_LEFT, dx)
        self._flush()

    def zoom(self, delta):
        self._lib.XTestFakeKeyEvent(self._display, _XKEY_CONTROL_L, True, 0)
        self._wheel(_XTEST_SCROLL_DOWN if delta > 0 else _XTEST_SCROLL_UP, delta)
        self._lib.XTestFakeKeyEvent(self._display, _XKEY_CONTROL_L, False, 0)
        self._flush()

    def position(self):
        return (self._x, self._y)

    def close(self):
        try:
            self._lib.XCloseDisplay(self._display)
        except Exception:
            pass


class _XTestLibAdapter:
    """Adapts raw ctypes libX11/libXtst to the small interface XTestMouse uses."""

    def __init__(self, x11, xtst):
        self._x11 = x11
        self._xtst = xtst
        self._qx = 0
        self._qy = 0

    def XOpenDisplay(self, name):
        return self._x11.XOpenDisplay(name.encode() if isinstance(name, str) else name)

    def XCloseDisplay(self, d):
        return self._x11.XCloseDisplay(d)

    def XTestFakeMotionEvent(self, d, screen, x, y, delay):
        return self._xtst.XTestFakeMotionEvent(d, screen, x, y, delay)

    def XTestFakeButtonEvent(self, d, button, press, delay):
        return self._xtst.XTestFakeButtonEvent(d, button, press, delay)

    def XTestFakeKeyEvent(self, d, keycode, press, delay):
        return self._xtst.XTestFakeKeyEvent(d, keycode, press, delay)

    def XFlush(self, d):
        return self._x11.XFlush(d)

    def XQueryPointer(self, d, root, win, rx, ry, wx, wy, mask):
        # Best-effort current position; failure just means we start at 0,0.
        try:
            root_x = ctypes.c_int()
            root_y = ctypes.c_int()
            win_x = ctypes.c_int()
            win_y = ctypes.c_int()
            same = ctypes.c_int()
            child = ctypes.c_void_p()
            mask_out = ctypes.c_uint()
            ok = self._x11.XQueryPointer(
                d, root, win,
                ctypes.byref(root_x), ctypes.byref(root_y),
                ctypes.byref(win_x), ctypes.byref(win_y),
                ctypes.byref(same), ctypes.byref(child), ctypes.byref(mask_out),
            )
            if ok:
                self._qx, self._qy = root_x.value, root_y.value
            return bool(ok)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Windows via SendInput
# ---------------------------------------------------------------------------

_WIN_MOVE = 0x0001
_WIN_LEFT_DOWN = 0x0002
_WIN_LEFT_UP = 0x0004
_WIN_RIGHT_DOWN = 0x0008
_WIN_RIGHT_UP = 0x0010
_WIN_MIDDLE_DOWN = 0x0020
_WIN_MIDDLE_UP = 0x0040
_WIN_WHEEL = 0x0800
_WIN_HWHEEL = 0x1000
_WIN_VK_CONTROL = 0x11
_WIN_WHEEL_NOTCH = 120


def _mouse_input(flags, dx=0, dy=0, data=0):
    return {"type": "mouse", "flags": flags, "dx": int(dx), "dy": int(dy), "data": int(data)}


def _key_input(vk, down):
    return {"type": "key", "vk": vk, "down": bool(down)}


class WindowsMouse(MouseBackend):
    """Windows mouse via SendInput. Pass *send* to inject a fake."""

    backend_name = "windows (SendInput)"

    def __init__(self, send=None):
        self._send = send if send is not None else _make_windows_send()
        self._x, self._y = 0.0, 0.0

    def move(self, dx, dy):
        self._x = max(0.0, self._x + dx)
        self._y = max(0.0, self._y + dy)
        self._send([_mouse_input(_WIN_MOVE, dx, dy)])

    def click(self, button="left", count=1):
        down, up = self._button_flags(button)
        for _ in range(count):
            self._send([_mouse_input(down), _mouse_input(up)])

    def button_down(self, button="left"):
        down, _ = self._button_flags(button)
        self._send([_mouse_input(down)])

    def button_up(self, button="left"):
        _, up = self._button_flags(button)
        self._send([_mouse_input(up)])

    @staticmethod
    def _button_flags(button):
        return {
            "left": (_WIN_LEFT_DOWN, _WIN_LEFT_UP),
            "right": (_WIN_RIGHT_DOWN, _WIN_RIGHT_UP),
            "middle": (_WIN_MIDDLE_DOWN, _WIN_MIDDLE_UP),
        }[button]

    def scroll(self, dx, dy):
        inputs = []
        if dy:
            # Windows wheel: positive data scrolls up; our dy>0 is down.
            inputs.append(_mouse_input(_WIN_WHEEL, data=-int(dy * 4)))
        if dx:
            inputs.append(_mouse_input(_WIN_HWHEEL, data=int(dx * 4)))
        if inputs:
            self._send(inputs)

    def zoom(self, delta):
        self._send(
            [
                _key_input(_WIN_VK_CONTROL, True),
                _mouse_input(_WIN_WHEEL, data=-int(delta * 4)),
                _key_input(_WIN_VK_CONTROL, False),
            ]
        )

    def position(self):
        return (self._x, self._y)


def _make_windows_send():
    """Build a real SendInput sender with ctypes structures."""
    user32 = ctypes.windll.user32

    class _MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_ulonglong),
        ]

    class _KeyInput(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_ulonglong),
        ]

    class _Input(ctypes.Union):
        _fields_ = [("mi", _MouseInput), ("ki", _KeyInput)]

    class _InputPacket(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("u", _Input)]

    def send(inputs):
        packets = []
        for item in inputs:
            pkt = _InputPacket()
            if item["type"] == "mouse":
                pkt.type = 0  # INPUT_MOUSE
                pkt.u.mi.dx = item["dx"]
                pkt.u.mi.dy = item["dy"]
                pkt.u.mi.mouseData = item["data"] & 0xFFFFFFFF
                pkt.u.mi.dwFlags = item["flags"]
            else:
                pkt.type = 1  # INPUT_KEYBOARD
                pkt.u.ki.wVk = item["vk"]
                pkt.u.ki.dwFlags = 0 if item["down"] else 0x0002  # KEYEVENTF_KEYUP
            packets.append(pkt)
        arr = (_InputPacket * len(packets))(*packets)
        user32.SendInput(len(packets), arr, ctypes.sizeof(_InputPacket))

    return send


# ---------------------------------------------------------------------------
# macOS via Quartz
# ---------------------------------------------------------------------------


class MacMouse(MouseBackend):
    """macOS mouse via Quartz CGEvents. Pass *post* to inject a fake."""

    backend_name = "macos (Quartz)"

    def __init__(self, post=None):
        self._post = post if post is not None else _make_mac_post()
        self._x, self._y = 0.0, 0.0

    def move(self, dx, dy):
        self._x = max(0.0, self._x + dx)
        self._y = max(0.0, self._y + dy)
        self._post({"type": "move", "x": self._x, "y": self._y})

    def click(self, button="left", count=1):
        for _ in range(count):
            self._post({"type": "down", "button": button, "x": self._x, "y": self._y})
            self._post({"type": "up", "button": button, "x": self._x, "y": self._y})

    def button_down(self, button="left"):
        self._post({"type": "down", "button": button, "x": self._x, "y": self._y})

    def button_up(self, button="left"):
        self._post({"type": "up", "button": button, "x": self._x, "y": self._y})

    def scroll(self, dx, dy):
        # Quartz wheel_dy: positive scrolls up; our dy>0 is down.
        self._post({"type": "scroll", "wheel_dy": -dy, "wheel_dx": dx})

    def zoom(self, delta):
        self._post({"type": "zoom", "delta": delta})

    def position(self):
        return (self._x, self._y)


def _make_mac_post():
    quartz = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    coref = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    quartz.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    quartz.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    quartz.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    quartz.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
    coref.CFRelease.argtypes = [ctypes.c_void_p]

    _LEFT_DOWN, _LEFT_UP = 1, 2
    _RIGHT_DOWN, _RIGHT_UP = 3, 4
    _MOVED = 5
    _SCROLL = 22
    _BUTTON_LEFT, _BUTTON_RIGHT = 0, 1
    _WHEEL_DY, _WHEEL_DX = 96, 87
    _FLAGS = 8  # kCGEventFlagMaskControl

    def post(ev):
        kind = ev["type"]
        if kind == "move":
            etype, button = _MOVED, _BUTTON_LEFT
        elif kind == "down":
            etype = _LEFT_DOWN if ev["button"] == "left" else _RIGHT_DOWN
            button = _BUTTON_LEFT if ev["button"] == "left" else _BUTTON_RIGHT
        elif kind == "up":
            etype = _LEFT_UP if ev["button"] == "left" else _RIGHT_UP
            button = _BUTTON_LEFT if ev["button"] == "left" else _BUTTON_RIGHT
        elif kind in ("scroll", "zoom"):
            etype, button = _SCROLL, _BUTTON_LEFT
        else:  # pragma: no cover
            return
        point = ctypes.c_void_p(0)
        event = quartz.CGEventCreateMouseEvent(None, etype, point, button)
        if not event:
            return
        try:
            if kind == "scroll":
                quartz.CGEventSetIntegerValueField(event, _WHEEL_DY, int(ev["wheel_dy"]))
                quartz.CGEventSetIntegerValueField(event, _WHEEL_DX, int(ev["wheel_dx"]))
            elif kind == "zoom":
                quartz.CGEventSetIntegerValueField(event, _WHEEL_DY, int(ev["delta"]))
                quartz.CGEventSetIntegerValueField(event, _FLAGS, 0x40000)  # ctrl
            quartz.CGEventPost(0, event)  # kCGHIDEventTap
        finally:
            coref.CFRelease(event)

    return post


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_mouse(prefer=None, env=None, platform_name=None, loaders=None):
    """Create the best available mouse backend.

    ``prefer`` forces a backend ("null", "xtest", "windows", "mac") and
    raises MouseUnavailable if it cannot be initialised. Otherwise the
    platform is auto-detected and we degrade gracefully to NullMouse
    (with a human-readable ``note``) when no real injection is possible —
    e.g. headless Linux or Wayland.
    """
    env = os.environ if env is None else env
    platform_name = platform.system() if platform_name is None else platform_name
    loaders = loaders or {}

    if prefer:
        name = prefer.lower()
        if name == "null":
            return NullMouse()
        defaults = {"xtest": XTestMouse, "windows": WindowsMouse, "mac": MacMouse, "macos": MacMouse}
        if name not in defaults and name not in loaders:
            raise ValueError("unknown mouse backend: %r" % prefer)
        factory = loaders.get(name) or defaults[name]
        return factory()

    if platform_name == "Linux":
        if env.get("DISPLAY"):
            try:
                return XTestMouse()
            except MouseUnavailable as exc:
                m = NullMouse()
                m.note = "XTest unavailable (%s); running in demo mode" % exc
                return m
        m = NullMouse()
        m.note = (
            "no DISPLAY set — headless or Wayland session; "
            "running in demo mode (virtual cursor, no real mouse control)"
        )
        return m
    if platform_name == "Windows":
        try:
            return WindowsMouse()
        except Exception as exc:  # pragma: no cover - non-Windows test envs
            m = NullMouse()
            m.note = "Windows backend unavailable (%s); demo mode" % exc
            return m
    if platform_name == "Darwin":
        try:
            return MacMouse()
        except Exception as exc:  # pragma: no cover - non-macOS test envs
            m = NullMouse()
            m.note = "macOS backend unavailable (%s); demo mode" % exc
            return m
    m = NullMouse()
    m.note = "unsupported platform %r; demo mode" % platform_name
    return m
