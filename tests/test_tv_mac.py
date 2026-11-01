import unittest
from unittest.mock import AsyncMock, patch
from backend.tv_control import Session, TVError


class TVMacTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_interface(self):
        session = Session(None)
        session.request = AsyncMock(return_value={"wifiInfo": {"macAddress": "02:0:0:0:0:1"}})
        self.assertEqual(await session.mac_address("tv.local"), "02:00:00:00:00:01")

    async def test_active_ip_disambiguates_wifi_from_wired(self):
        session = Session(None)
        session.request = AsyncMock(side_effect=[
            {"wifiInfo": {"macAddress": "02:00:00:00:00:01"}, "wiredInfo": {"macAddress": "02:00:00:00:00:02"}},
            {"wifi": {"state": "connected", "ipAddress": "192.0.2.10"},
             "wired": {"state": "connected", "ipAddress": "192.0.2.11"}},
        ])
        with patch("backend.tv_control.resolve_ipv4", AsyncMock(return_value="192.0.2.10")):
            self.assertEqual(await session.mac_address("tv.local"), "02:00:00:00:00:01")

    async def test_mismatching_ip_never_guesses_an_interface(self):
        session = Session(None)
        session.request = AsyncMock(side_effect=[
            {"wifiInfo": {"macAddress": "02:00:00:00:00:01"}, "wiredInfo": {"macAddress": "02:00:00:00:00:02"}},
            {"wifi": {"state": "disconnected"}, "wired": {"state": "connected", "ipAddress": "192.0.2.99"}},
        ])
        with patch("backend.tv_control.resolve_ipv4", AsyncMock(return_value="192.0.2.10")):
            self.assertIsNone(await session.mac_address("tv.local"))

    async def test_legacy_status_endpoint(self):
        session = Session(None)
        session.request = AsyncMock(side_effect=[
            {"wifiInfo": {"macAddress": "02:00:00:00:00:01"}, "wiredInfo": {"macAddress": "02:00:00:00:00:02"}},
            TVError("Unavailable"), {"wired": {"state": "connected", "ipAddress": "192.0.2.10"}},
        ])
        with patch("backend.tv_control.resolve_ipv4", AsyncMock(return_value="192.0.2.10")):
            self.assertEqual(await session.mac_address("tv.local"), "02:00:00:00:00:02")

    async def test_missing_or_invalid_macs(self):
        for info in ({}, {"wifiInfo": None}, {"wifiInfo": {"macAddress": "ff:ff:ff:ff:ff:ff"}}):
            session = Session(None)
            session.request = AsyncMock(return_value=info)
            self.assertIsNone(await session.mac_address("tv.local"))
