"""Tests for phone_mouse.protocol — message schema, encoding, validation."""
import unittest

from phone_mouse.protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    decode_message,
    encode_message,
    make_message,
    validate_message,
)


class TestMakeMessage(unittest.TestCase):
    def test_defaults(self):
        m = make_message("ping")
        self.assertEqual(m["t"], "ping")
        self.assertEqual(m["seq"], 0)
        self.assertIsInstance(m["ts"], float)
        self.assertEqual(m["data"], {})

    def test_explicit_fields(self):
        m = make_message("touch_move", {"points": [{"id": 1, "x": 1, "y": 2}]}, seq=7, ts=123.25)
        self.assertEqual(m["seq"], 7)
        self.assertEqual(m["ts"], 123.25)

    def test_unknown_type_rejected(self):
        with self.assertRaises(ProtocolError):
            make_message("nope")

    def test_negative_seq_rejected(self):
        with self.assertRaises(ProtocolError):
            make_message("ping", seq=-1)

    def test_bool_seq_rejected(self):
        # bool is a subclass of int in Python — must not sneak through.
        with self.assertRaises(ProtocolError):
            make_message("ping", seq=True)

    def test_non_finite_ts_rejected(self):
        with self.assertRaises(ProtocolError):
            make_message("ping", ts=float("nan"))
        with self.assertRaises(ProtocolError):
            make_message("ping", ts=float("inf"))


class TestRoundTrip(unittest.TestCase):
    def test_touch_roundtrip(self):
        m = make_message(
            "touch_move",
            {"points": [{"id": 1, "x": 10.5, "y": 20.0}, {"id": 2, "x": 30, "y": 40}]},
            seq=7,
            ts=123.25,
        )
        raw = encode_message(m)
        self.assertIsInstance(raw, bytes)
        self.assertEqual(decode_message(raw), m)

    def test_hello_roundtrip(self):
        m = make_message("hello", {"token": "abc123", "name": "my phone", "version": 1}, seq=1, ts=1.0)
        self.assertEqual(decode_message(encode_message(m)), m)

    def test_state_roundtrip(self):
        m = make_message("state", {"x": 960.0, "y": 540.0, "buttons": []}, seq=2, ts=2.0)
        self.assertEqual(decode_message(encode_message(m)), m)

    def test_ping_roundtrip(self):
        m = make_message("ping", {"ts": 42.5}, seq=3, ts=42.5)
        self.assertEqual(decode_message(encode_message(m)), m)


class TestDecodeRejects(unittest.TestCase):
    def assertBad(self, raw):
        with self.assertRaises(ProtocolError):
            decode_message(raw)

    def test_invalid_json(self):
        self.assertBad(b"not json at all")

    def test_non_dict_json(self):
        self.assertBad(b"[1, 2, 3]")

    def test_unknown_type(self):
        self.assertBad(b'{"t": "wat", "seq": 0, "ts": 0, "data": {}}')

    def test_missing_seq(self):
        self.assertBad(b'{"t": "ping", "ts": 0, "data": {}}')

    def test_missing_type(self):
        self.assertBad(b'{"seq": 0, "ts": 0, "data": {}}')

    def test_nan_rejected(self):
        # Python's json module accepts NaN by default — we must not.
        self.assertBad(b'{"t": "ping", "seq": 0, "ts": NaN, "data": {}}')

    def test_infinity_rejected(self):
        self.assertBad(b'{"t": "ping", "seq": 0, "ts": Infinity, "data": {}}')

    def test_oversized_payload_rejected(self):
        self.assertBad(b"x" * (MAX_MESSAGE_BYTES + 1))

    def test_touch_empty_points(self):
        self.assertBad(b'{"t": "touch_move", "seq": 0, "ts": 0, "data": {"points": []}}')

    def test_touch_too_many_points(self):
        pts = ",".join('{"id": %d, "x": 1, "y": 1}' % i for i in range(6))
        self.assertBad(('{"t": "touch_move", "seq": 0, "ts": 0, "data": {"points": [%s]}}' % pts).encode())

    def test_touch_coord_wrong_type(self):
        self.assertBad(b'{"t": "touch_move", "seq": 0, "ts": 0, "data": {"points": [{"id": 1, "x": "10", "y": 1}]}}')

    def test_touch_missing_id(self):
        self.assertBad(b'{"t": "touch_move", "seq": 0, "ts": 0, "data": {"points": [{"x": 1, "y": 1}]}}')

    def test_touch_coord_out_of_range(self):
        self.assertBad(b'{"t": "touch_move", "seq": 0, "ts": 0, "data": {"points": [{"id": 1, "x": -5, "y": 1}]}}')
        self.assertBad(b'{"t": "touch_move", "seq": 0, "ts": 0, "data": {"points": [{"id": 1, "x": 99999, "y": 1}]}}')

    def test_hello_token_not_string(self):
        self.assertBad(b'{"t": "hello", "seq": 0, "ts": 0, "data": {"token": 123}}')

    def test_hello_token_empty(self):
        self.assertBad(b'{"t": "hello", "seq": 0, "ts": 0, "data": {"token": ""}}')

    def test_encode_rejects_nan(self):
        with self.assertRaises(ProtocolError):
            encode_message({"t": "ping", "seq": 0, "ts": float("nan"), "data": {}})

    def test_validate_accepts_valid(self):
        validate_message(make_message("touch_start", {"points": [{"id": 0, "x": 0, "y": 0}]}))
        validate_message(make_message("bye"))


if __name__ == "__main__":
    unittest.main()
