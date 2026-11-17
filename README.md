# 📱 Phone Mouse — your phone is your laptop's touchpad

Use your phone's screen as a real-time touchpad for your laptop. No app to
install, no dependencies to pip-install — just Python 3 (standard library
only) on the laptop and a web page on the phone, linked over Wi-Fi with a
WebSocket.

## Quick start

**On your laptop:**

```bash
python3 server.py
```

It prints a URL like `http://192.168.1.10:8765/?token=AbC123…` and the
mouse backend it detected.

**On your phone** (same Wi-Fi as the laptop): open that URL. The touchpad
is live instantly — the token in the URL pre-pairs the phone.

> **Try it in the browser first:** `python3 server.py --demo` runs with a
> virtual cursor (token `demo`) so you can test the page on any device
> without touching the real mouse.

## Gestures

| Gesture | Action |
|---|---|
| 1 finger drag | Move cursor (relative, with pointer acceleration) |
| Tap | Left click |
| Double-tap | Double click |
| Press & hold still | Drag (press-and-hold) |
| 2 fingers drag | Scroll |
| 2 fingers flick + release | Scroll with momentum |
| Pinch / spread | Zoom (Ctrl + scroll) |
| 2-finger tap | Right click |

On the page you can also adjust **speed**, **invert Y**, and use the
Left/Right/Double click buttons. The screen stays awake while connected
(Wake Lock), and the link auto-reconnects if Wi-Fi blips.

## Mouse backends (auto-detected)

| Platform | Backend | Notes |
|---|---|---|
| Linux (X11) | XTest via ctypes | Direct X calls — touchpad-level latency |
| Windows | `SendInput` via ctypes | Relative moves + wheel |
| macOS | Quartz `CGEvent` via ctypes | Native events |
| Headless / Wayland / unknown | Demo (virtual cursor) | Server explains why; page shows the virtual cursor |

Force one with `--mouse null|xtest|windows|mac`. Wayland has no global
input injection without a portal, so on Wayland the server runs in demo
mode — run an X11 session (or XWayland) for real control.

## Options

```
python3 server.py --port 8765 --token MYTOKEN --sensitivity 1.5
python3 server.py --demo          # virtual cursor, token "demo"
```

## How it works

```
phone browser ──raw touches──▶ WebSocket ──▶ gesture engine ──▶ OS mouse
 (thin forwarder)              (RFC 6455,     (tap/scroll/pinch/   (XTest /
                                stdlib only)   inertia/accel)       SendInput /
                                                                     Quartz)
```

- The phone sends **raw touches**; one gesture engine on the laptop
  interprets them — a single source of truth for touchpad behaviour.
- **Real-time feel:** moves are coalesced to animation frames, sequence
  numbers drop duplicates/out-of-order packets, and pointer acceleration
  makes slow drags precise and fast flicks long — like a real touchpad.
- **Managed connection:** token pairing, heartbeat ping/pong with latency
  readout, dead-connection timeout, single-controller takeover (a fresh
  connection evicts a stale one), and auto-reconnect with backoff.
- **Zero dependencies:** the WebSocket layer is hand-rolled RFC 6455 on
  the standard library, so `python3 server.py` just works.

## Development (test-driven)

```bash
./run_tests.sh
```

128 tests cover the protocol schema, the gesture engine (fake-clock,
deterministic), RFC 6455 framing (including the RFC's own test vectors),
session auth/heartbeat/sequencing, every mouse backend's event translation
(via injected fakes — no display needed), and end-to-end WebSocket tests
against a live server.

## Troubleshooting

- **Phone can't reach the URL** — same Wi-Fi? Firewall blocking the port?
  Allow Python on your firewall for the chosen port.
- **Cursor doesn't move on the laptop** — check the backend line at
  startup. On Linux, `echo $DISPLAY` must be set (X11 session).
- **Page loads but says "enter pairing token"** — open the full URL
  including `?token=…`, or type the token shown in the server banner.
