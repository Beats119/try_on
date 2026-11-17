"""Tests for phone_mouse.websocket — RFC 6455 framing implemented on stdlib."""
import unittest

from phone_mouse.websocket import (
    FrameDecoder,
    WSHandshakeError,
    WSProtocolError,
    accept_key,
    build_handshake_response,
    encode_frame,
    parse_handshake_request,
)


class TestHandshake(unittest.TestCase):
    def test_rfc_accept_vector(self):
        # RFC 6455 section 1.3 example.
        self.assertEqual(
            accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
        )

    def test_response_is_101_with_accept(self):
        resp = build_handshake_response("dGhlIHNhbXBsZSBub25jZQ==")
        self.assertTrue(resp.startswith(b"HTTP/1.1 101"))
        self.assertIn(b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", resp)

    def test_parse_valid_request(self):
        raw = (
            b"GET /ws?token=abc HTTP/1.1\r\n"
            b"Host: 192.168.1.10:8765\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"\r\n"
        )
        info = parse_handshake_request(raw)
        self.assertEqual(info["key"], "dGhlIHNhbXBsZSBub25jZQ==")
        self.assertEqual(info["path"], "/ws?token=abc")

    def test_parse_missing_key_raises(self):
        raw = b"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
        with self.assertRaises(WSHandshakeError):
            parse_handshake_request(raw)

    def test_parse_non_get_raises(self):
        raw = (
            b"POST /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        )
        with self.assertRaises(WSHandshakeError):
            parse_handshake_request(raw)

    def test_parse_missing_upgrade_raises(self):
        raw = b"GET /ws HTTP/1.1\r\nHost: x\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        with self.assertRaises(WSHandshakeError):
            parse_handshake_request(raw)


class TestFrames(unittest.TestCase):
    def test_encode_unmasked_hello(self):
        self.assertEqual(encode_frame(b"Hello"), b"\x81\x05Hello")

    def test_rfc_masked_hello(self):
        # RFC 6455 section 5.7: masked "Hello" from a client.
        dec = FrameDecoder(expect_mask=True)
        msgs = dec.feed(bytes.fromhex("818537fa213d7f9f4d5158"))
        self.assertEqual(msgs, [(1, b"Hello")])

    def test_masked_roundtrip(self):
        frame = encode_frame(b"hi there", mask_key=b"\x01\x02\x03\x04")
        dec = FrameDecoder(expect_mask=True)
        self.assertEqual(dec.feed(frame), [(1, b"hi there")])

    def test_unmasked_roundtrip_server_side(self):
        frame = encode_frame(b"server says hi")
        dec = FrameDecoder(expect_mask=False)
        self.assertEqual(dec.feed(frame), [(1, b"server says hi")])

    def test_unmasked_client_frame_rejected(self):
        dec = FrameDecoder(expect_mask=True)
        with self.assertRaises(WSProtocolError):
            dec.feed(encode_frame(b"no mask"))

    def test_masked_frame_rejected_when_not_expected(self):
        dec = FrameDecoder(expect_mask=False)
        with self.assertRaises(WSProtocolError):
            dec.feed(encode_frame(b"masked", mask_key=b"\x01\x02\x03\x04"))

    def test_16bit_length(self):
        payload = b"a" * 1000
        dec = FrameDecoder(expect_mask=False)
        self.assertEqual(dec.feed(encode_frame(payload)), [(1, payload)])

    def test_64bit_length(self):
        payload = b"b" * 70000
        dec = FrameDecoder(expect_mask=False)
        self.assertEqual(dec.feed(encode_frame(payload)), [(1, payload)])

    def test_fragmented_message_reassembled(self):
        dec = FrameDecoder(expect_mask=False)
        self.assertEqual(dec.feed(encode_frame(b"Hel", fin=False)), [])
        self.assertEqual(
            dec.feed(encode_frame(b"lo", opcode=0, fin=True)),
            [(1, b"Hello")],
        )

    def test_continuation_without_start_rejected(self):
        dec = FrameDecoder(expect_mask=False)
        with self.assertRaises(WSProtocolError):
            dec.feed(encode_frame(b"orphan", opcode=0))

    def test_ping_pong_close_pass_through(self):
        dec = FrameDecoder(expect_mask=False)
        msgs = dec.feed(encode_frame(b"abc", opcode=9))
        msgs += dec.feed(encode_frame(b"", opcode=10))
        msgs += dec.feed(encode_frame(b"", opcode=8))
        self.assertEqual(msgs, [(9, b"abc"), (10, b""), (8, b"")])

    def test_fragmented_control_frame_rejected(self):
        dec = FrameDecoder(expect_mask=False)
        with self.assertRaises(WSProtocolError):
            dec.feed(encode_frame(b"x", opcode=9, fin=False))

    def test_byte_by_byte_feed(self):
        frame = encode_frame(b"streamed", mask_key=b"\x09\x08\x07\x06")
        dec = FrameDecoder(expect_mask=True)
        out = []
        for i in range(len(frame)):
            out += dec.feed(frame[i : i + 1])
        self.assertEqual(out, [(1, b"streamed")])

    def test_multiple_frames_in_one_feed(self):
        dec = FrameDecoder(expect_mask=False)
        data = encode_frame(b"one") + encode_frame(b"two")
        self.assertEqual(dec.feed(data), [(1, b"one"), (1, b"two")])

    def test_oversized_payload_rejected(self):
        dec = FrameDecoder(expect_mask=False, max_payload=1024)
        with self.assertRaises(WSProtocolError):
            dec.feed(encode_frame(b"x" * 2048))

    def test_unknown_opcode_rejected(self):
        dec = FrameDecoder(expect_mask=False)
        with self.assertRaises(WSProtocolError):
            dec.feed(encode_frame(b"x", opcode=3))


if __name__ == "__main__":
    unittest.main()
