"""Wire protocol between the phone page and the laptop server.

Every message is a JSON object::

    {"t": <type>, "seq": <int>, "ts": <float seconds>, "data": {...}}

Types:
    hello        phone -> server   {"token", "name", "version"}
    touch_start  phone -> server   {"points": [{"id","x","y"}, ...]}
    touch_move   phone -> server   {"points": [...]}
    touch_end    phone -> server   {"points": [...]}
    ping/pong    both directions   {"ts": <float>}
    ack          server -> phone   {"ok": true, "backend": <str>}
    state        server -> phone   {"x", "y", "buttons"}  (demo cursor)
    error        server -> phone   {"code", "message"}
    bye          phone -> server   {}

The phone sends *raw touches*; the laptop's gesture engine interprets them.
That keeps one source of truth for touchpad behaviour and makes the client
a thin, hard-to-break forwarder.

All validation lives here so a malformed or hostile client can never crash
the server: :func:`decode_message` raises :class:`ProtocolError` on anything
unexpected, including NaN/Infinity (which Python's json module accepts by
default) and oversized payloads.
"""
import json
import math
import time

MAX_MESSAGE_BYTES = 64 * 1024
MAX_POINTS = 5
MAX_COORD = 10000.0

MESSAGE_TYPES = frozenset(
    {
        "hello",
        "touch_start",
        "touch_move",
        "touch_end",
        "ping",
        "pong",
        "ack",
        "state",
        "error",
        "bye",
    }
)

_TOUCH_TYPES = frozenset({"touch_start", "touch_move", "touch_end"})


class ProtocolError(ValueError):
    """Raised when a message fails validation."""


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_finite_number(v, what):
    if not _is_number(v) or not math.isfinite(v):
        raise ProtocolError("%s must be a finite number" % what)


def _validate_points(data):
    points = data.get("points")
    if not isinstance(points, list) or not (1 <= len(points) <= MAX_POINTS):
        raise ProtocolError("points must be a list of 1..%d entries" % MAX_POINTS)
    for p in points:
        if not isinstance(p, dict):
            raise ProtocolError("each point must be an object")
        pid = p.get("id")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid < 0:
            raise ProtocolError("point id must be a non-negative integer")
        for axis in ("x", "y"):
            v = p.get(axis)
            _check_finite_number(v, "point %s" % axis)
            if not (0 <= v <= MAX_COORD):
                raise ProtocolError("point %s out of range" % axis)


def _validate_data(mtype, data):
    if not isinstance(data, dict):
        raise ProtocolError("data must be an object")
    if mtype in _TOUCH_TYPES:
        _validate_points(data)
    elif mtype == "hello":
        token = data.get("token")
        if not isinstance(token, str) or not token or len(token) > 256:
            raise ProtocolError("hello token must be a non-empty string")
        name = data.get("name", "")
        if not isinstance(name, str) or len(name) > 64:
            raise ProtocolError("hello name must be a string of at most 64 chars")
        version = data.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool):
            raise ProtocolError("hello version must be an integer")
    elif mtype in ("ping", "pong"):
        if "ts" in data:
            _check_finite_number(data["ts"], "ping ts")
    elif mtype == "state":
        for axis in ("x", "y"):
            _check_finite_number(data.get(axis), "state %s" % axis)
    elif mtype == "error":
        if not isinstance(data.get("code"), str):
            raise ProtocolError("error code must be a string")
        if not isinstance(data.get("message", ""), str):
            raise ProtocolError("error message must be a string")
    elif mtype == "ack":
        if "ok" in data and not isinstance(data["ok"], bool):
            raise ProtocolError("ack ok must be a boolean")


def validate_message(msg):
    """Raise ProtocolError unless *msg* is a well-formed protocol message."""
    if not isinstance(msg, dict):
        raise ProtocolError("message must be a JSON object")
    mtype = msg.get("t")
    if mtype not in MESSAGE_TYPES:
        raise ProtocolError("unknown message type: %r" % (mtype,))
    seq = msg.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise ProtocolError("seq must be a non-negative integer")
    ts = msg.get("ts")
    _check_finite_number(ts, "ts")
    if ts < 0:
        raise ProtocolError("ts must be non-negative")
    _validate_data(mtype, msg.get("data", {}))


def make_message(mtype, data=None, seq=0, ts=None):
    """Build (and validate) a message dict."""
    msg = {
        "t": mtype,
        "seq": seq,
        "ts": time.time() if ts is None else ts,
        "data": {} if data is None else data,
    }
    validate_message(msg)
    return msg


def encode_message(msg):
    """Validate and serialise a message to UTF-8 JSON bytes."""
    validate_message(msg)
    return json.dumps(msg, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _reject_constant(name):
    raise ProtocolError("non-finite number %s is not allowed" % name)


def decode_message(raw):
    """Parse and validate raw bytes into a message dict.

    Raises ProtocolError on oversized payloads, invalid JSON, non-finite
    numbers, or any schema violation.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise ProtocolError("message must be bytes")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message too large: %d bytes" % len(raw))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("message is not valid UTF-8") from exc
    try:
        msg = json.loads(text, parse_constant=_reject_constant)
    except ProtocolError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError("invalid JSON: %s" % exc) from exc
    validate_message(msg)
    return msg
