"""Network address discovery: Wi-Fi *and* Bluetooth PAN.

Why Bluetooth PAN? A phone browser cannot open raw Bluetooth sockets
(SPP needs a native app; Web Bluetooth is BLE-only and missing on iOS
Safari). But Bluetooth **PAN tethering** gives the phone a plain IP
address over Bluetooth — and then the exact same WebSocket server works
unchanged, with latency that is stable (~10-30ms) and completely
independent of Wi-Fi congestion.

This module finds the laptop's addresses so the server banner can print
both the Wi-Fi URL and (when present) the Bluetooth PAN URL:

- Android: Settings → Bluetooth tethering, pair with the laptop.
- Linux laptop: BlueZ PAN (`bnep0` interface) via NetworkManager/BlueZ.
- Windows/macOS: Bluetooth PAN / Internet sharing.

Everything here is best-effort and never raises: discovery must not be
able to crash the server.
"""
import socket

from .http_server import lan_ip  # noqa: F401  (re-exported for convenience)

_BLUETOOTH_MARKERS = ("bt", "bnep", "pan", "bluetooth")


def is_bluetooth_interface(name):
    """True if an interface name looks like a Bluetooth PAN interface."""
    lowered = (name or "").lower()
    return any(marker in lowered for marker in _BLUETOOTH_MARKERS)


def _interface_ips():
    """Best-effort [(name, ipv4-or-None)] for all interfaces. Never raises."""
    out = []
    try:
        names = socket.if_nameindex()
    except Exception:
        return out
    for name in names:
        out.append((name, _ipv4_for_interface(name)))
    return out


def _ipv4_for_interface(name):
    """IPv4 address of one interface, or None. Linux ioctl; None elsewhere."""
    try:
        import fcntl
        import struct

        SIOCGIFADDR = 0x8915
        packed = fcntl.ioctl(
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM),
            SIOCGIFADDR,
            struct.pack("16sH", name.encode()[:15], socket.AF_INET),
        )
        ip = socket.inet_ntoa(packed[20:24])
        return ip if ip != "0.0.0.0" else None
    except Exception:
        return None


def bluetooth_ip(ifaces=None):
    """IPv4 of the Bluetooth PAN interface, or None if not connected.

    ``ifaces`` is an injectable [(name, ip-or-None)] list for testing;
    by default the real interfaces are enumerated.
    """
    if ifaces is None:
        ifaces = _interface_ips()
    for name, ip in ifaces:
        if ip and not ip.startswith("127.") and is_bluetooth_interface(name):
            return ip
    return None


def candidate_ips(ifaces=None, lan=None):
    """All usable (non-loopback) IPv4 addresses, deduplicated.

    ``lan`` is the primary Wi-Fi/LAN address (defaults to lan_ip());
    ``ifaces`` is injectable for testing.
    """
    if ifaces is None:
        ifaces = _interface_ips()
    if lan is None:
        lan = lan_ip()
    ips = []
    if lan and not lan.startswith("127."):
        ips.append(lan)
    for _name, ip in ifaces:
        if ip and not ip.startswith("127.") and ip not in ips:
            ips.append(ip)
    return ips
