<div align="center">

<img src="assets/hero.png" alt="Phone Mouse — your phone is your laptop's touchpad" width="100%">

# 📱 Phone Mouse

### *Your phone. Your touchpad. Zero installs.*

Turn your phone's screen into a **real-time touchpad** for your laptop —
with pointer acceleration, scroll momentum, pinch-zoom, and drag, just like
the real thing. No app to install. No dependencies to pip-install.
Just `python3 server.py` on the laptop and a web page on the phone.

<br>

![Python](https://img.shields.io/badge/Python-3.8%2B-3776ab?style=for-the-badge&logo=python&logoColor=white)
![Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen?style=for-the-badge)
![Tests](https://img.shields.io/badge/tests-155%20passing-4f9cf9?style=for-the-badge)
![Protocol](https://img.shields.io/badge/WebSocket-RFC%206455-0b0e14?style=for-the-badge)
![Platforms](https://img.shields.io/badge/Linux%20·%20Windows%20·%20macOS-8b98a9?style=for-the-badge)

<br>

[**⚡ Quick Start**](#-quick-start-30-seconds) ·
[**🖱️ Gestures**](#️-gestures) ·
[**🏗️ How It Works**](#️-how-it-works) ·
[**🧪 Development**](#-development-test-driven) ·
[**🛠️ Troubleshooting**](#️-troubleshooting)

</div>

---

## ✨ Why Phone Mouse?

| | |
|---|---|
| ⚡ **Real-time** | WebSocket link with animation-frame coalescing — feels like a built-in touchpad, not a remote app |
| 🪶 **Zero dependencies** | Pure Python standard library. The WebSocket layer is hand-rolled RFC 6455 — `python3 server.py` just works |
| 🎯 **True touchpad feel** | Relative movement, pointer acceleration, scroll inertia, long-press drag — one gesture engine, fully tested |
| 🔒 **Paired & managed** | Token pairing, heartbeat + live latency, dead-connection timeout, auto-reconnect with backoff |
| 🖥️ **Native mouse control** | Linux XTest · Windows `SendInput` · macOS Quartz — auto-detected, with graceful demo fallback |
| 📲 **No app install** | The phone just opens a web page. Works on any modern mobile browser |

---

## 🚀 Quick Start (30 seconds)

**1. On your laptop:**

```bash
python3 server.py
```

It prints a URL like `http://192.168.1.10:8765/?token=AbC123…` and the
mouse backend it detected.

**2. On your phone** (same Wi-Fi as the laptop): open that URL.
The touchpad is live instantly — the token in the URL pre-pairs the phone.

> 💡 **Try it first in your browser:** `python3 server.py --demo` runs with a
> virtual cursor (token `demo`), so you can test the page on any device
> without touching the real mouse.

---

## 📶 Slow Wi-Fi? Switch to Bluetooth (PAN)

Wi-Fi congestion can make latency spike from ~5ms to seconds. **Bluetooth
PAN tethering** gives you the *same server* over Bluetooth with stable
~10–30ms latency — completely independent of Wi-Fi.

**Setup (Android phone):**

1. Phone: **Settings → Bluetooth tethering** → turn it on, pair with your laptop.
2. Laptop: accept the PAN connection (Linux: BlueZ/NetworkManager `bnep0`;
   Windows/macOS: Bluetooth PAN).
3. Restart `python3 server.py` — the banner now shows a **📶 Bluetooth URL**
   alongside the Wi-Fi one.
4. Open the Bluetooth URL on your phone. Same touchpad, no Wi-Fi needed.

> **Why not "direct" Bluetooth?** A phone *browser* can't open raw
> Bluetooth sockets: SPP needs a native app, and Web Bluetooth is BLE-only
> (and missing on iOS Safari). PAN is the clever zero-dependency route —
> it gives the phone a plain IP over Bluetooth, so the existing WebSocket
> server works unchanged.

### Why latency spikes — and how it's fixed

| Cause | Fix |
|---|---|
| Nagle's algorithm batching small packets (+40–200ms/move) | `TCP_NODELAY` on every WebSocket socket |
| Browser queueing seconds of stale moves when the network stalls | Client backpressure: drop stale moves, always send only the latest position |
| Demo cursor echo flooding the link on every move | Server throttles state echoes to 20Hz (the mouse itself is never throttled) |

> 💡 **Tip:** prefer 5GHz Wi-Fi over 2.4GHz, and keep the phone/laptop
> close — Bluetooth PAN is the fallback when the network is congested.

---

## 🖱️ Gestures

| Gesture | Action |
|---|---|
| ☝️ 1-finger drag | Move cursor *(relative, with pointer acceleration)* |
| 👆 Tap | Left click |
| 👆👆 Double-tap | Double click |
| ✋ Press & hold still | Drag *(press-and-hold)* |
| ✌️ 2-finger drag | Scroll *(smart mode-lock: scroll & pinch never collide)* |
| ✌️💨 2-finger flick + release | Scroll with momentum |
| 🤏 Pinch / spread | Zoom *(Ctrl + scroll)* |
| ✌️👆 2-finger tap | Right click |

On the page you can also adjust **speed**, **invert Y**, and use the
Left / Right / Double click buttons. The screen stays awake while
connected (Wake Lock), and the link auto-reconnects if Wi-Fi blips.

---

## 🏗️ How It Works

```mermaid
flowchart LR
    Phone["📱 Phone browser<br/>thin forwarder"] -- "raw touches" --> WS["WebSocket<br/>RFC 6455 · stdlib only"]
    WS --> Engine["Gesture engine<br/>tap · scroll · pinch · inertia · accel"]
    Engine --> Mouse["OS mouse<br/>XTest / SendInput / Quartz"]
```

- The phone streams **raw touches**; one gesture engine on the laptop
  interprets them — a single source of truth for touchpad behaviour.
- **Real-time feel:** moves are coalesced to animation frames, sequence
  numbers drop duplicates / out-of-order packets, and pointer acceleration
  makes slow drags precise and fast flicks long — like a real touchpad.
- **Managed connection:** token pairing, heartbeat ping/pong with latency
  readout, dead-connection timeout, single-controller takeover (a fresh
  connection evicts a stale one), and auto-reconnect with backoff.
- **Latency-hardened:** `TCP_NODELAY`, client backpressure (stale moves
  are dropped, never queued), and throttled demo echoes — plus optional
  Bluetooth PAN transport when Wi-Fi is congested.
- **Zero dependencies:** the WebSocket layer is hand-rolled RFC 6455 on
  the standard library, so `python3 server.py` just works.

### Mouse backends (auto-detected)

| Platform | Backend | Notes |
|---|---|---|
| 🐧 Linux (X11) | XTest via ctypes | Direct X calls — touchpad-level latency |
| 🪟 Windows | `SendInput` via ctypes | Relative moves + wheel |
| 🍎 macOS | Quartz `CGEvent` via ctypes | Native events |
| 🖥️ Headless / Wayland / unknown | Demo *(virtual cursor)* | Server explains why; page shows the virtual cursor |

Force one with `--mouse null|xtest|windows|mac`. Wayland has no global
input injection without a portal, so on Wayland the server runs in demo
mode — run an X11 session (or XWayland) for real control.

### Options

```bash
python3 server.py --port 8765 --token MYTOKEN --sensitivity 1.5
python3 server.py --demo          # virtual cursor, token "demo"
```

---

## 🧪 Development (test-driven)

Built test-first: **155 tests** covering the protocol schema, the gesture
engine (fake-clock, deterministic), RFC 6455 framing (including the RFC's
own test vectors), session auth / heartbeat / sequencing, every mouse
backend's event translation (via injected fakes — no display needed), and
end-to-end WebSocket tests against a live server.

```bash
./run_tests.sh
```

```
Ran 128 tests in 7.0s
OK
```

---

## 🛠️ Troubleshooting

<details>
<summary><b>Phone can't reach the URL</b></summary>

Same Wi-Fi? Firewall blocking the port? Allow Python on your firewall
for the chosen port (default `8765`).

</details>

<details>
<summary><b>Cursor doesn't move on the laptop</b></summary>

Check the backend line at startup. On Linux, `echo $DISPLAY` must be set
(X11 session). On Wayland, use an X11 session or `--demo` mode.

</details>

<details>
<summary><b>Scary <code>ConnectionResetError: [WinError 10054]</code> tracebacks?</b></summary>

Harmless noise — phone browsers reset idle HTTP connections constantly.
The server keeps running (these are silenced in current versions). If the
phone shows *connected* but nothing moves, the link is half-open: the
page now detects missing pongs and auto-reconnects. Just keep the page
open and it will recover on its own.

</details>

<details>
<summary><b>Page says "enter pairing token"</b></summary>

Open the full URL including `?token=…`, or type the token shown in the
server banner on the laptop.

</details>

---

<div align="center">

**Built with Python's standard library only — no installs, no fuss.** 🐍✨

*Your phone is already in your pocket. Now it's a touchpad too.*

</div>
