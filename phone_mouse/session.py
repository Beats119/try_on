"""Per-connection session: auth, sequencing, heartbeat, event plumbing.

A Session owns one phone connection. It:

- requires a ``hello`` with the server token before any touch is accepted
  (unless the connection was pre-authenticated via ``?token=`` in the URL),
- drops duplicate/out-of-order touch messages by sequence number (a
  retransmitted move must not move the cursor twice),
- answers pings with pongs and sends its own keepalive pings,
- closes connections that go silent past ``heartbeat_timeout``,
- feeds touches through the gesture engine into the mouse backend, and
  (in demo mode) reports the virtual cursor position back to the page.

Sessions are transport-agnostic: :meth:`Session.handle_message` takes a
decoded protocol dict and returns a list of reply dicts to serialise.
"""
import hmac
import time

from .gestures import GestureEngine
from .mouse import NullMouse
from .protocol import make_message

_TOUCH_TYPES = frozenset({"touch_start", "touch_move", "touch_end"})


class Session:
    def __init__(
        self,
        token,
        mouse,
        gesture=None,
        heartbeat_interval=5.0,
        heartbeat_timeout=15.0,
        preauthed=False,
    ):
        self.token = token
        self.mouse = mouse
        self.gesture = gesture if gesture is not None else GestureEngine()
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.authed = preauthed
        self.closed = False
        self._last_seq = -1
        self._last_activity = None
        self._last_ping_sent = None
        try:
            self._last_state_pos = mouse.position()
        except Exception:
            self._last_state_pos = None

    # -- public API --------------------------------------------------------

    def handle_message(self, msg, now=None):
        """Process one decoded message; return reply messages to send."""
        if self.closed:
            return []
        now = time.monotonic() if now is None else now
        self._last_activity = now
        if self._last_ping_sent is None:
            self._last_ping_sent = now
        mtype = msg["t"]

        if not self.authed:
            if mtype != "hello":
                self.closed = True
                return [self.error_message("auth_required", "first message must be hello")]
            token = msg["data"].get("token", "")
            if not hmac.compare_digest(str(token), str(self.token)):
                self.closed = True
                return [self.error_message("auth_failed", "invalid token")]
            self.authed = True
            return self.welcome_messages()

        if mtype == "hello":
            return self.welcome_messages()
        if mtype == "ping":
            return [make_message("pong", {"ts": msg["data"].get("ts", now)}, seq=msg["seq"], ts=now)]
        if mtype == "pong":
            return []
        if mtype == "bye":
            self.closed = True
            return []
        if mtype in _TOUCH_TYPES:
            if msg["seq"] <= self._last_seq:
                return []  # duplicate / out-of-order: drop, never double-apply
            self._last_seq = msg["seq"]
            events = self.gesture.handle(mtype, msg["data"]["points"], now)
            self._apply(events)
            return self._maybe_state()
        return []  # ack/state/error from a client: ignore

    def tick(self, now):
        """Periodic housekeeping: heartbeat, gesture timers, timeout."""
        if self.closed:
            return []
        if self._last_activity is None:
            self._last_activity = now
        if now - self._last_activity > self.heartbeat_timeout:
            self.closed = True
            return [self.error_message("timeout", "connection timed out")]
        out = []
        events = self.gesture.tick(now)
        if events:
            self._apply(events)
            out.extend(self._maybe_state())
        if self._last_ping_sent is None or now - self._last_ping_sent >= self.heartbeat_interval:
            out.append(make_message("ping", {"ts": now}, ts=now))
            self._last_ping_sent = now
        return out

    # -- internals ---------------------------------------------------------

    def welcome_messages(self):
        out = [
            make_message(
                "ack",
                {"ok": True, "backend": self.mouse.backend_name},
                ts=time.time(),
            )
        ]
        out.extend(self._maybe_state(force=True))
        return out

    def _apply(self, events):
        for ev in events:
            if ev.kind == "move":
                self.mouse.move(ev.dx, ev.dy)
            elif ev.kind == "click":
                self.mouse.click(ev.button, ev.count)
            elif ev.kind == "double_click":
                self.mouse.click(ev.button, 2)
            elif ev.kind == "right_click":
                self.mouse.click("right", 1)
            elif ev.kind == "button_down":
                self.mouse.button_down(ev.button)
            elif ev.kind == "button_up":
                self.mouse.button_up(ev.button)
            elif ev.kind == "scroll":
                self.mouse.scroll(ev.dx, ev.dy)
            elif ev.kind == "zoom":
                self.mouse.zoom(ev.dx)

    def _maybe_state(self, force=False):
        """In demo mode, echo the virtual cursor so the page can draw it."""
        if not isinstance(self.mouse, NullMouse):
            return []
        pos = self.mouse.position()
        if not force and pos == self._last_state_pos:
            return []
        self._last_state_pos = pos
        return [make_message("state", {"x": pos[0], "y": pos[1], "buttons": []}, ts=time.time())]

    @staticmethod
    def error_message(code, message):
        return make_message("error", {"code": code, "message": message}, ts=time.time())
