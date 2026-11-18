"""Tests for phone_mouse.discovery — finding Wi-Fi and Bluetooth PAN addresses.

Bluetooth PAN (tethering) gives the phone an IP over Bluetooth, so the
exact same WebSocket server works over Bluetooth with zero extra code on
the wire. Discovery just has to *find* that address and surface it.
"""
import unittest

from phone_mouse.discovery import bluetooth_ip, candidate_ips, is_bluetooth_interface


class TestIsBluetoothInterface(unittest.TestCase):
    def test_linux_bnep(self):
        self.assertTrue(is_bluetooth_interface("bnep0"))

    def test_bt_prefix(self):
        self.assertTrue(is_bluetooth_interface("bt-pan"))
        self.assertTrue(is_bluetooth_interface("bluetooth0"))

    def test_case_insensitive(self):
        self.assertTrue(is_bluetooth_interface("BNEP0"))

    def test_wifi_is_not_bluetooth(self):
        self.assertFalse(is_bluetooth_interface("wlan0"))
        self.assertFalse(is_bluetooth_interface("en0"))
        self.assertFalse(is_bluetooth_interface("eth0"))
        self.assertFalse(is_bluetooth_interface("lo"))


class TestBluetoothIp(unittest.TestCase):
    def test_finds_bnep_address(self):
        ifaces = [("wlan0", "192.168.1.5"), ("bnep0", "172.17.0.1")]
        self.assertEqual(bluetooth_ip(ifaces), "172.17.0.1")

    def test_none_when_no_bluetooth_interface(self):
        ifaces = [("wlan0", "192.168.1.5"), ("eth0", "10.0.0.2")]
        self.assertIsNone(bluetooth_ip(ifaces))

    def test_ignores_bluetooth_interface_without_address(self):
        ifaces = [("bnep0", None), ("wlan0", "192.168.1.5")]
        self.assertIsNone(bluetooth_ip(ifaces))

    def test_ignores_loopback(self):
        ifaces = [("lo", "127.0.0.1")]
        self.assertIsNone(bluetooth_ip(ifaces))


class TestCandidateIps(unittest.TestCase):
    def test_merges_lan_and_interface_addresses(self):
        ifaces = [("wlan0", "192.168.1.5"), ("bnep0", "172.17.0.1"), ("lo", "127.0.0.1")]
        ips = candidate_ips(ifaces=ifaces, lan="192.168.1.5")
        self.assertIn("192.168.1.5", ips)
        self.assertIn("172.17.0.1", ips)
        self.assertNotIn("127.0.0.1", ips)

    def test_deduplicates(self):
        ifaces = [("wlan0", "192.168.1.5"), ("eth0", "192.168.1.5")]
        ips = candidate_ips(ifaces=ifaces, lan="192.168.1.5")
        self.assertEqual(ips.count("192.168.1.5"), 1)

    def test_real_enumeration_returns_something_sane(self):
        # On any machine this must not crash and must never return loopback.
        ips = candidate_ips()
        self.assertIsInstance(ips, list)
        self.assertNotIn("127.0.0.1", ips)


if __name__ == "__main__":
    unittest.main()
