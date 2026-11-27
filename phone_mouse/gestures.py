"""Touch -> mouse gesture engine: the touchpad feel, in pure Python.

The phone streams raw touches (``touch_start`` / ``touch_move`` /
``touch_end`` with finger id + x/y). This engine turns them into mouse
events, mimicking a real laptop touchpad:

- **One finger** moves the cursor *relatively* (like a touchpad, not a
  touchscreen), with pointer acceleration: slow drags are precise (1x),
  fast flicks travel up to 3.5x further.
- **Tap** = left click, **double-tap** = double click.
- **Long press** (hold still) = press-and-hold drag (button down, move,
  release) — like touchpad drag lock.
- **Two fingers**: tap = right click, move together = scroll (with
  momentum/inertia on release), pinch = ctrl+scroll zoom. Scroll and
  pinch are *disambiguated by mode-locking*: whichever gesture clearly
  wins first locks in for the whole two-finger session, so scrolling
  never triggers accidental zoom (and vice versa).
- Lifting one of two fingers hands control to the remaining finger as a
  plain cursor mover (its tap is cancelled, so no surprise clicks).

The engine is a pure function of (event, timestamp): the caller passes
``now`` (monotonic seconds), which makes every behaviour deterministic and
unit-testable with a fake clock. :meth:`GestureEngine.tick` must be called
periodically so long-press and scroll inertia can fire between touches.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class MouseEvent:
    """One interpreted mouse action.

    ``kind`` is one of: move, click, double_click, right_click,
    button_down, button_up, scroll, zoom.
    For scroll, ``dy > 0`` means scroll down. For zoom, ``dx`` is the
    pinch delta (positive = fingers apart = zoom in).
    """

    kind: str
    dx: float = 0.0
    dy: float = 0.0
    button: str = "left"
    count: int = 1


class _Finger:
    __slots__ = (
        "x", "y", "lx", "ly",
        "start_x", "start_y", "start_time", "last_time",
        "max_disp", "tap_cancelled",
    )

    def __init__(self, x, y, now):
        self.x = x
        self.y = y
        self.lx = x  # last reported position (for move deltas)
        self.ly = y
        self.start_x = x
        self.start_y = y
        self.start_time = now
        self.last_time = now
        self.max_disp = 0.0
        self.tap_cancelled = False

    def move_to(self, x, y):
        self.max_disp = max(self.max_disp, math.hypot(x - self.start_x, y - self.start_y))
        self.x = x
        self.y = y


class GestureEngine:
    def __init__(
        self,
        sensitivity=1.0,
        scroll_sensitivity=1.0,
        tap_timeout=0.25,
        double_tap_timeout=0.35,
        long_press_timeout=0.5,
        tap_slop=12.0,
        accel=True,
        scroll_lock_threshold=8.0,
        pinch_lock_threshold=8.0,
    ):
        self.sensitivity = sensitivity
        self.scroll_sensitivity = scroll_sensitivity
        self.tap_timeout = tap_timeout
        self.double_tap_timeout = double_tap_timeout
        self.long_press_timeout = long_press_timeout
        self.tap_slop = tap_slop
        self.accel = accel
        self.scroll_lock_threshold = scroll_lock_threshold
        self.pinch_lock_threshold = pinch_lock_threshold

        self._fingers = {}  # id -> _Finger (max 2 tracked)
        self._ignored = set()  # ids of 3rd+ fingers
        self._dragging = False
        self._last_tap_end = None
        # Two-finger state
        self._prev_centroid = None
        self._prev_dist = None
        self._last_two_time = None
        self._two_mode = None  # None (undecided) | "scroll" | "zoom"
        self._two_start_centroid = None
        self._two_start_dist = None
        self._pending_two = None  # info about first-lifted finger of a pair
        # Scroll inertia
        self._scroll_vx = 0.0
        self._scroll_vy = 0.0
        self._inertia_active = False
        self._inertia_last = None

    # -- public API --------------------------------------------------------

    def handle(self, msg_type, points, now):
        """Process one touch message; return a list of MouseEvent."""
        if msg_type == "touch_start":
            return self._on_start(points, now)
        if msg_type == "touch_move":
            return self._on_move(points, now)
        if msg_type == "touch_end":
            return self._on_end(points, now)
        return []

    def tick(self, now):
        """Fire time-based events (long press, scroll inertia)."""
        events = []
        # Long-press drag: one finger held still past the threshold.
        if len(self._fingers) == 1 and not self._dragging:
            finger = next(iter(self._fingers.values()))
            if (
                not finger.tap_cancelled
                and finger.max_disp <= self.tap_slop
                and now - finger.start_time >= self.long_press_timeout
            ):
                self._dragging = True
                events.append(MouseEvent("button_down", button="left"))
        # Scroll inertia: decaying momentum after a fast two-finger flick.
        if self._inertia_active:
            dt = now - self._inertia_last if self._inertia_last is not None else 0.0
            if dt > 0:
                self._inertia_last = now
                events.append(
                    MouseEvent("scroll", dx=self._scroll_vx * dt, dy=self._scroll_vy * dt)
                )
                decay = math.exp(-3.0 * dt)
                self._scroll_vx *= decay
                self._scroll_vy *= decay
                if math.hypot(self._scroll_vx, self._scroll_vy) < 10.0:
                    self._inertia_active = False
        return events

    # -- touch_start -------------------------------------------------------

    def _on_start(self, points, now):
        events = []
        for p in points:
            fid = p["id"]
            if fid in self._fingers or fid in self._ignored:
                continue
            if len(self._fingers) >= 2:
                self._ignored.add(fid)
                continue
            self._fingers[fid] = _Finger(p["x"], p["y"], now)
            if len(self._fingers) == 2:
                # Entering two-finger mode: cancel any pending single-finger
                # tap, and end an in-progress drag cleanly.
                for f in self._fingers.values():
                    f.tap_cancelled = True
                if self._dragging:
                    self._dragging = False
                    events.append(MouseEvent("button_up", button="left"))
                self._pending_two = None
                self._prev_centroid = self._centroid()
                self._prev_dist = self._dist()
                self._last_two_time = now
                self._two_mode = None
                self._two_start_centroid = self._prev_centroid
                self._two_start_dist = self._prev_dist
                self._scroll_vx = 0.0
                self._scroll_vy = 0.0
                self._inertia_active = False
        return events

    # -- touch_move --------------------------------------------------------

    def _on_move(self, points, now):
        events = []
        moved = False
        for p in points:
            finger = self._fingers.get(p["id"])
            if finger is None:
                continue
            finger.move_to(p["x"], p["y"])
            moved = True
        if not moved:
            return []

        if len(self._fingers) == 1:
            finger = next(iter(self._fingers.values()))
            dx_raw = finger.x - finger.lx
            dy_raw = finger.y - finger.ly
            dt = max(now - finger.last_time, 1e-3)
            finger.last_time = now
            finger.lx = finger.x
            finger.ly = finger.y
            if self._dragging:
                factor = 1.0  # precise dragging: no acceleration
            elif self.accel:
                speed = math.hypot(dx_raw, dy_raw) / dt
                factor = 1.0 + min(2.5, speed / 500.0)
            else:
                factor = 1.0
            events.append(
                MouseEvent(
                    "move",
                    dx=dx_raw * self.sensitivity * factor,
                    dy=dy_raw * self.sensitivity * factor,
                )
            )
        elif len(self._fingers) == 2:
            centroid = self._centroid()
            dist = self._dist()
            if self._prev_centroid is not None:
                cdx = centroid[0] - self._prev_centroid[0]
                cdy = centroid[1] - self._prev_centroid[1]
                ddist = dist - self._prev_dist if self._prev_dist is not None else 0.0

                if self._two_mode is None:
                    # Disambiguate by cumulative displacement from the
                    # session start: nothing fires until one gesture
                    # clearly wins, then it locks for the session.
                    scroll_disp = math.hypot(
                        centroid[0] - self._two_start_centroid[0],
                        centroid[1] - self._two_start_centroid[1],
                    )
                    pinch_disp = abs(dist - self._two_start_dist)
                    scroll_wins = scroll_disp >= self.scroll_lock_threshold
                    pinch_wins = pinch_disp >= self.pinch_lock_threshold
                    if scroll_wins and pinch_wins:
                        self._two_mode = (
                            "scroll"
                            if scroll_disp / self.scroll_lock_threshold
                            >= pinch_disp / self.pinch_lock_threshold
                            else "zoom"
                        )
                    elif scroll_wins:
                        self._two_mode = "scroll"
                    elif pinch_wins:
                        self._two_mode = "zoom"

                if self._two_mode == "scroll":
                    if abs(cdx) >= 0.5 or abs(cdy) >= 0.5:
                        events.append(
                            MouseEvent(
                                "scroll",
                                dx=cdx * self.scroll_sensitivity,
                                dy=cdy * self.scroll_sensitivity,
                            )
                        )
                        # Track scroll velocity (EMA) for inertia.
                        dt = max(now - self._last_two_time, 1e-3) if self._last_two_time else 0.05
                        self._scroll_vx = 0.7 * self._scroll_vx + 0.3 * (cdx / dt)
                        self._scroll_vy = 0.7 * self._scroll_vy + 0.3 * (cdy / dt)
                elif self._two_mode == "zoom":
                    if abs(ddist) >= 1.0:
                        events.append(MouseEvent("zoom", dx=ddist))
            self._prev_centroid = centroid
            self._prev_dist = dist
            self._last_two_time = now
        return events

    # -- touch_end ---------------------------------------------------------

    def _on_end(self, points, now):
        events = []
        for p in points:
            fid = p["id"]
            if fid in self._ignored:
                self._ignored.discard(fid)
                continue
            finger = self._fingers.pop(fid, None)
            if finger is None:
                continue
            duration = now - finger.start_time

            if len(self._fingers) == 1:
                # 2 -> 1: remember the lifted finger for a possible
                # two-finger tap; the remaining finger takes over as a
                # plain cursor mover with its tap cancelled.
                self._pending_two = {
                    "duration": duration,
                    "max_disp": finger.max_disp,
                    "end_time": now,
                }
                remaining = next(iter(self._fingers.values()))
                remaining.tap_cancelled = True
                remaining.start_time = now
                remaining.lx = remaining.x
                remaining.ly = remaining.y
                remaining.last_time = now
                self._prev_centroid = None
                self._prev_dist = None
                self._two_mode = None
            elif len(self._fingers) == 0:
                events.extend(self._on_all_up(finger, duration, now))
        return events

    def _on_all_up(self, last_finger, duration, now):
        events = []
        if self._pending_two is not None:
            # We had two fingers. Right-click only if BOTH were quick taps.
            first = self._pending_two
            self._pending_two = None
            both_quick = (
                first["duration"] <= self.tap_timeout
                and duration <= self.tap_timeout
                and first["max_disp"] <= self.tap_slop
                and last_finger.max_disp <= self.tap_slop
            )
            if both_quick:
                events.append(MouseEvent("right_click", button="right"))
            # Scroll inertia from a fast flick (keep velocity for tick()).
            if math.hypot(self._scroll_vx, self._scroll_vy) > 50.0:
                self._inertia_active = True
                self._inertia_last = now
            else:
                self._scroll_vx = 0.0
                self._scroll_vy = 0.0
            return events

        if self._dragging:
            self._dragging = False
            events.append(MouseEvent("button_up", button="left"))
            return events

        if (
            not last_finger.tap_cancelled
            and duration <= self.tap_timeout
            and last_finger.max_disp <= self.tap_slop
        ):
            if self._last_tap_end is not None and now - self._last_tap_end <= self.double_tap_timeout:
                events.append(MouseEvent("double_click", button="left", count=2))
                self._last_tap_end = None
            else:
                events.append(MouseEvent("click", button="left", count=1))
                self._last_tap_end = now
        return events

    # -- helpers -----------------------------------------------------------

    def _centroid(self):
        xs = [f.x for f in self._fingers.values()]
        ys = [f.y for f in self._fingers.values()]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def _dist(self):
        f1, f2 = list(self._fingers.values())[:2]
        return math.hypot(f1.x - f2.x, f1.y - f2.y)
