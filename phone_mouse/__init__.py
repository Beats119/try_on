"""Phone Mouse — use your phone's screen as your laptop's touchpad.

Zero-dependency (Python standard library only) implementation:

- :mod:`phone_mouse.protocol`   — validated JSON message schema
- :mod:`phone_mouse.websocket`  — RFC 6455 framing on raw sockets
- :mod:`phone_mouse.gestures`   — touch -> mouse gesture engine (pure, tested)
- :mod:`phone_mouse.mouse`      — OS mouse injection (XTest / Windows / macOS / demo)
- :mod:`phone_mouse.session`    — auth, sequencing, heartbeat per connection
- :mod:`phone_mouse.http_server`— HTTP + WebSocket server serving the touchpad page
"""

__version__ = "1.0.0"
