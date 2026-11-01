import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend.network import (detect_mac, neighbour_mac, normalize_mac, resolve_ipv4, ip_query,
                             macos_route_interface, macos_arp_mac)


ADDRESS = "192.0.2.10"
MAC = "02:ab:cd:12:34:56"


class NeighbourTests(unittest.TestCase):
    def entry(self, **values):
        return {"dst": ADDRESS, "dev": "wlan0", "state": ["REACHABLE"], "lladdr": MAC, **values}

    def test_normalizes_unicast_mac(self):
        self.assertEqual(normalize_mac(MAC), MAC.upper())

    def test_rejects_invalid_zero_and_multicast(self):
        for value in (None, "bad", "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:01"):
            self.assertIsNone(normalize_mac(value))

    def test_exact_target_and_interface_only(self):
        entries = [self.entry(dst="192.0.2.1"), self.entry(dev="eth0"), self.entry()]
        self.assertEqual(neighbour_mac(entries, ADDRESS, "wlan0"), MAC.upper())

    def test_failed_incomplete_and_router_rejected(self):
        for changes in ({"state": ["FAILED"]}, {"state": ["INCOMPLETE"]}, {"state": []},
                        {"router": True}, {"lladdr": "00:00:00:00:00:00"}):
            self.assertIsNone(neighbour_mac([self.entry(**changes)], ADDRESS, "wlan0"))

    def test_ambiguous_macs_rejected(self):
        self.assertIsNone(neighbour_mac([self.entry(), self.entry(lladdr="02:00:00:00:00:01")], ADDRESS, "wlan0"))

    def test_stale_entry_and_string_state_supported(self):
        self.assertEqual(neighbour_mac([self.entry(state="STALE")], ADDRESS, "wlan0"), MAC.upper())


class DetectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        platform = patch("backend.network.platform.system", return_value="Linux")
        platform.start()
        self.addCleanup(platform.stop)

    async def test_detects_direct_neighbour(self):
        entries = [{"dst": ADDRESS, "dev": "wlan0", "state": ["REACHABLE"], "lladdr": MAC}]
        with patch("backend.network.resolve_ipv4", AsyncMock(return_value=ADDRESS)), \
             patch("backend.network.ip_query", AsyncMock(side_effect=[[{"dev": "wlan0"}], entries])) as query, \
             patch("backend.network.probe", AsyncMock()) as probe:
            self.assertEqual(await detect_mac("tv.local"), MAC.upper())
            probe.assert_awaited_once_with(ADDRESS)
            query.assert_any_await("neigh", "show", "to", ADDRESS, "dev", "wlan0")

    async def test_gateway_mac_is_never_used(self):
        with patch("backend.network.resolve_ipv4", AsyncMock(return_value=ADDRESS)), \
             patch("backend.network.ip_query", AsyncMock(return_value=[{"dev": "wlan0", "gateway": "192.0.2.1"}])), \
             patch("backend.network.probe", AsyncMock()) as probe:
            self.assertIsNone(await detect_mac(ADDRESS))
            probe.assert_not_awaited()

    async def test_unavailable_network_tools_fail_gracefully(self):
        with patch("backend.network.resolve_ipv4", AsyncMock(return_value=ADDRESS)), \
             patch("backend.network.ip_query", AsyncMock(return_value=[])):
            self.assertIsNone(await detect_mac(ADDRESS))

    async def test_missing_arp_entry_returns_none(self):
        with patch("backend.network.resolve_ipv4", AsyncMock(return_value=ADDRESS)), \
             patch("backend.network.ip_query", AsyncMock(side_effect=[[{"dev": "wlan0"}], [], []])), \
             patch("backend.network.probe", AsyncMock()):
            self.assertIsNone(await detect_mac(ADDRESS))

    async def test_dns_failure_returns_none(self):
        with patch("backend.network.resolve_ipv4", AsyncMock(side_effect=OSError)):
            self.assertIsNone(await detect_mac("tv.local"))

    async def test_ambiguous_dns_rejected(self):
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", AsyncMock(return_value=[(0, 0, 0, "", (ADDRESS, 0)),
                (0, 0, 0, "", ("192.0.2.11", 0))])):
            self.assertIsNone(await resolve_ipv4("tv.local"))

    async def test_timed_out_subprocess_is_reaped(self):
        process = AsyncMock()
        process.returncode = None
        from unittest.mock import Mock
        process.kill = Mock()
        process.communicate.side_effect = TimeoutError
        with patch("backend.network.shutil.which", return_value="/usr/bin/ip"), \
             patch("backend.network.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            with self.assertRaises(TimeoutError):
                await ip_query("route", "get", ADDRESS)
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()


class MacOSTests(unittest.IsolatedAsyncioTestCase):
    ROUTE = "route to: 192.0.2.10\ninterface: en0\nflags: <UP,HOST,DONE,LLINFO,IFSCOPE>\n"
    ARP = "? (192.0.2.10) at 2:ab:cd:12:34:6 on en0 ifscope [ethernet]\n"

    def test_direct_route(self):
        self.assertEqual(macos_route_interface(self.ROUTE), "en0")

    def test_gateway_and_invalid_routes_rejected(self):
        for flags in ("UP,GATEWAY,DONE", "UP,REJECT", "UP,BLACKHOLE", "UP,LOCAL", ""):
            self.assertIsNone(macos_route_interface(f"interface: en0\nflags: <{flags}>\n"))
        self.assertIsNone(macos_route_interface(""))
        self.assertIsNone(macos_route_interface("interface: lo0\nflags: <UP>"))

    def test_unpadded_mac_normalized(self):
        self.assertEqual(macos_arp_mac(self.ARP, ADDRESS, "en0"), "02:AB:CD:12:34:06")

    def test_wrong_ip_or_interface_rejected(self):
        self.assertIsNone(macos_arp_mac(self.ARP, "192.0.2.1", "en0"))
        self.assertIsNone(macos_arp_mac(self.ARP, ADDRESS, "en1"))

    def test_incomplete_and_multicast_rejected(self):
        for mac in ("(incomplete)", "ff:ff:ff:ff:ff:ff", "0:0:0:0:0:0"):
            self.assertIsNone(macos_arp_mac(f"? ({ADDRESS}) at {mac} on en0", ADDRESS, "en0"))

    def test_conflicting_macs_rejected(self):
        self.assertIsNone(macos_arp_mac(self.ARP + f"? ({ADDRESS}) at 2:0:0:0:0:1 on en0\n", ADDRESS, "en0"))

    async def test_macos_uses_native_commands(self):
        with patch("backend.network.platform.system", return_value="Darwin"), \
             patch("backend.network.resolve_ipv4", AsyncMock(return_value=ADDRESS)), \
             patch("backend.network.probe", AsyncMock()), \
             patch("backend.network.command_output", AsyncMock(side_effect=[self.ROUTE, self.ARP])) as command:
            self.assertEqual(await detect_mac(ADDRESS), "02:AB:CD:12:34:06")
            command.assert_any_await("/sbin/route", "-n", "get", ADDRESS)
            command.assert_any_await("/usr/sbin/arp", "-n", "-i", "en0", ADDRESS)

    async def test_macos_does_not_probe_gateway_route(self):
        with patch("backend.network.platform.system", return_value="Darwin"), \
             patch("backend.network.resolve_ipv4", AsyncMock(return_value=ADDRESS)), \
             patch("backend.network.command_output", AsyncMock(return_value="interface: en0\nflags: <UP,GATEWAY>")), \
             patch("backend.network.probe", AsyncMock()) as probe:
            self.assertIsNone(await detect_mac(ADDRESS))
            probe.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
