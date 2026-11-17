"""Minimal RFC 6455 WebSocket implementation on the standard library.

The server needs WebSockets but must run anywhere with just ``python3`` —
no ``pip install websockets``. This module implements exactly what we need:

- HTTP Upgrade handshake (SHA-1 + base64 accept key, RFC 6455 section 1.3)
- Frame encoding/decoding with 7/16/64-bit lengths and masking
- Fragmented message reassembly, ping/pong/close control frames
- Strict validation: clients MUST mask, control frames MUST NOT fragment,
  payloads are size-capped. Anything else raises :class:`WSProtocolError`.

The decoder is incremental: feed it arbitrary chunks (even one byte at a
time) and it yields complete ``(opcode, payload)`` messages.
"""
import base64
import hashlib
import struct

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME_PAYLOAD = 1 << 20  # 1 MiB

OP_CONTINUATION = 0
OP_TEXT = 1
OP_BINARY = 2
OP_CLOSE = 8
OP_PING = 9
OP_PONG = 10

_VALID_OPCODES = frozenset({OP_CONTINUATION, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG})
_CONTROL_OPCODES = frozenset({OP_CLOSE, OP_PING, OP_PONG})


class WSHandshakeError(ValueError):
    """Raised when an HTTP Upgrade request is not a valid WebSocket handshake."""


class WSProtocolError(ValueError):
    """Raised when a frame violates RFC 6455."""


def accept_key(client_key):
    """Compute Sec-WebSocket-Accept for a client key (RFC 6455 section 1.3)."""
    digest = hashlib.sha1((client_key.strip() + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def build_handshake_response(client_key):
    """Serialise the 101 Switching Protocols response."""
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: %s\r\n"
        "\r\n" % accept_key(client_key)
    ).encode("ascii")


def parse_handshake_request(raw):
    """Parse a raw HTTP request head into method/path/headers/key.

    Raises WSHandshakeError if it is not a valid WebSocket upgrade request.
    """
    text = raw.decode("iso-8859-1")
    lines = text.split("\r\n")
    if not lines:
        raise WSHandshakeError("empty request")
    parts = lines[0].split()
    if len(parts) != 3:
        raise WSHandshakeError("malformed request line")
    method, path, _version = parts
    if method != "GET":
        raise WSHandshakeError("WebSocket handshake must be GET")
    headers = {}
    for line in lines[1:]:
        if not line:
            break
        if ":" not in line:
            raise WSHandshakeError("malformed header: %r" % line)
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    if "websocket" not in headers.get("upgrade", "").lower():
        raise WSHandshakeError("missing Upgrade: websocket")
    key = headers.get("sec-websocket-key", "")
    if not key:
        raise WSHandshakeError("missing Sec-WebSocket-Key")
    return {"method": method, "path": path, "headers": headers, "key": key}


def encode_frame(payload, opcode=OP_TEXT, fin=True, mask_key=None):
    """Serialise one frame. Pass *mask_key* (4 bytes) for client->server frames."""
    if opcode not in _VALID_OPCODES:
        raise WSProtocolError("invalid opcode %r" % opcode)
    if opcode in _CONTROL_OPCODES and (not fin or len(payload) > 125):
        raise WSProtocolError("control frames must be final and <= 125 bytes")
    first = (0x80 if fin else 0x00) | opcode
    length = len(payload)
    mask_bit = 0x80 if mask_key else 0x00
    if length < 126:
        header = struct.pack("!BB", first, mask_bit | length)
    elif length < 65536:
        header = struct.pack("!BBH", first, mask_bit | 126, length)
    else:
        header = struct.pack("!BBQ", first, mask_bit | 127, length)
    if mask_key:
        if len(mask_key) != 4:
            raise WSProtocolError("mask key must be 4 bytes")
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        return header + mask_key + masked
    return header + payload


class FrameDecoder:
    """Incremental frame decoder with fragmentation reassembly.

    ``expect_mask=True`` for data received *from a client* (RFC 6455
    section 5.1: clients MUST mask). ``False`` for data from a server.
    """

    def __init__(self, expect_mask=True, max_payload=MAX_FRAME_PAYLOAD):
        self.expect_mask = expect_mask
        self.max_payload = max_payload
        self._buffer = b""
        self._frag_opcode = None
        self._frag_payload = b""

    def feed(self, data):
        """Consume bytes; return a list of complete (opcode, payload) messages."""
        self._buffer += data
        out = []
        while True:
            parsed = self._try_parse_one()
            if parsed is None:
                break
            fin, opcode, payload = parsed
            if opcode in _CONTROL_OPCODES:
                out.append((opcode, payload))
            elif opcode == OP_CONTINUATION:
                if self._frag_opcode is None:
                    raise WSProtocolError("continuation frame without a fragmented message")
                self._frag_payload += payload
                if len(self._frag_payload) > self.max_payload:
                    raise WSProtocolError("fragmented message too large")
                if fin:
                    out.append((self._frag_opcode, self._frag_payload))
                    self._frag_opcode = None
                    self._frag_payload = b""
            else:  # OP_TEXT or OP_BINARY
                if self._frag_opcode is not None:
                    raise WSProtocolError("new data frame during fragmented message")
                if fin:
                    out.append((opcode, payload))
                else:
                    self._frag_opcode = opcode
                    self._frag_payload = payload
        return out

    def _try_parse_one(self):
        """Parse one frame from the buffer, or return None if incomplete."""
        buf = self._buffer
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if opcode not in _VALID_OPCODES:
            raise WSProtocolError("invalid opcode %d" % opcode)
        if opcode in _CONTROL_OPCODES and not fin:
            raise WSProtocolError("control frames must not be fragmented")
        if self.expect_mask and not masked:
            raise WSProtocolError("client frames must be masked")
        if not self.expect_mask and masked:
            raise WSProtocolError("server frames must not be masked")

        offset = 2
        if length == 126:
            if len(buf) < offset + 2:
                return None
            length = struct.unpack("!H", buf[offset : offset + 2])[0]
            offset += 2
        elif length == 127:
            if len(buf) < offset + 8:
                return None
            length = struct.unpack("!Q", buf[offset : offset + 8])[0]
            offset += 8
        if length > self.max_payload:
            raise WSProtocolError("frame payload too large: %d" % length)
        if opcode in _CONTROL_OPCODES and length > 125:
            raise WSProtocolError("control frame payload too large")

        mask_key = None
        if masked:
            if len(buf) < offset + 4:
                return None
            mask_key = buf[offset : offset + 4]
            offset += 4
        if len(buf) < offset + length:
            return None
        payload = buf[offset : offset + length]
        self._buffer = buf[offset + length :]
        if mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload
