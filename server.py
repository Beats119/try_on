#!/usr/bin/env python3
"""Phone Mouse server — run this on your laptop, open the URL on your phone.

Usage:
    python3 server.py [--host 0.0.0.0] [--port 8765] [--token TOKEN]
                      [--sensitivity 1.0] [--mouse auto|null|xtest|windows|mac]
                      [--demo]

Zero dependencies: Python 3.8+ standard library only.
"""
import argparse
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from phone_mouse.http_server import TouchpadHTTPServer, lan_ip
from phone_mouse.mouse import create_mouse


def main(argv=None):
    parser = argparse.ArgumentParser(description="Use your phone as a laptop touchpad.")
    parser.add_argument("--host", default="0.0.0.0", help="interface to bind (default: all)")
    parser.add_argument("--port", type=int, default=8765, help="port to listen on (default: 8765)")
    parser.add_argument("--token", default=None, help="pairing token (default: random)")
    parser.add_argument("--sensitivity", type=float, default=1.0, help="cursor speed multiplier")
    parser.add_argument(
        "--mouse",
        default="auto",
        choices=["auto", "null", "xtest", "windows", "mac"],
        help="mouse backend (default: auto-detect)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="demo mode: virtual cursor, no real mouse control (token defaults to 'demo')",
    )
    args = parser.parse_args(argv)

    token = args.token or ("demo" if args.demo else secrets.token_urlsafe(9))
    prefer = "null" if args.demo else (None if args.mouse == "auto" else args.mouse)
    mouse = create_mouse(prefer)
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    server = TouchpadHTTPServer(
        args.host,
        args.port,
        token,
        mouse,
        static_dir,
        gesture_kwargs={"sensitivity": args.sensitivity},
    )
    actual_port = server.server_address[1]
    ip = lan_ip()

    print()
    print("=" * 62)
    print("  PHONE MOUSE — your phone is now a touchpad")
    print("=" * 62)
    print("  Mouse backend : %s" % mouse.backend_name)
    if getattr(mouse, "note", ""):
        print("  Note          : %s" % mouse.note)
    print()
    print("  1. Connect your phone to the SAME Wi-Fi as this laptop.")
    print("  2. Open this URL on your phone:")
    print()
    print("     http://%s:%d/?token=%s" % (ip, actual_port, token))
    print()
    print("  Token (if asked): %s" % token)
    print("  Status page     : http://%s:%d/health" % (ip, actual_port))
    print()
    print("  Gestures: 1 finger = move | tap = click | double-tap = double click")
    print("            hold still = drag | 2 fingers = scroll / pinch-zoom")
    print("            2-finger tap = right click")
    print()
    print("  Press Ctrl+C to stop.")
    print("=" * 62)
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Bye!")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
