import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager
from types import SimpleNamespace

from backend.controller import Controller
from backend.settings import Settings


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller()
        self.controller._configure(Settings(self.directory.name))

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_manual_mode_starts_no_automation_task(self):
        self.assertIsNone(self.controller.automation.resume_task)
        self.assertIsNone(self.controller.lifecycle.bus)

    async def test_settings_persist_without_exposing_key(self):
        values = self.controller.settings.values.copy()
        values["host"] = "tv.local"
        self.assertTrue((await self.controller.save_settings(values))["ok"])
        self.controller.settings.save(key="private-key", replace_key=True)
        state = await self.controller.get_state()
        self.assertTrue(state["settings"]["paired"])
        self.assertNotIn("private-key", str(state))
        self.assertEqual(Settings(self.directory.name).values["host"], "tv.local")

    async def test_wake_uses_real_adapter_without_requiring_display(self):
        self.controller.tv.wake = AsyncMock()
        self.assertTrue((await self.controller.action("wake"))["ok"])
        self.controller.tv.wake.assert_awaited_once()

    async def test_mac_detection_returns_result_without_saving_it(self):
        with patch("backend.controller.detect_mac", AsyncMock(return_value="02:00:00:00:00:01")):
            result = await self.controller.detect_tv_mac("tv.local")
        self.assertTrue(result["ok"])
        self.assertEqual(self.controller.settings.values["mac"], "")

    async def test_invalid_host_not_queried(self):
        with patch("backend.controller.detect_mac", AsyncMock()) as detect:
            self.assertFalse((await self.controller.detect_tv_mac("https://tv.local/"))["ok"])
            detect.assert_not_awaited()

    async def test_missing_arp_requests_pairing_without_opening_a_tv_prompt(self):
        with patch("backend.controller.detect_mac", AsyncMock(return_value=None)), \
             patch.object(self.controller.tv, "session") as session:
            result = await self.controller.detect_tv_mac("tv.local")
        self.assertFalse(result["ok"])
        self.assertTrue(result["pairing_required"])
        session.assert_not_called()

    def fake_session(self, mac="02:00:00:00:00:01"):
        @asynccontextmanager
        async def session(*args, **kwargs):
            yield SimpleNamespace(key="new-key", mac_address=AsyncMock(return_value=mac))
        return session

    async def test_pairing_fills_mac_and_persists_it(self):
        self.controller.settings.save({**self.controller.settings.values, "host": "tv.local"})
        with patch.object(self.controller.tv, "session", self.fake_session()):
            result = await self.controller.action("pair")
        self.assertTrue(result["ok"])
        self.assertEqual(self.controller.settings.key, "new-key")
        self.assertEqual(Settings(self.directory.name).values["mac"], "02:00:00:00:00:01")

    async def test_unsupported_mac_does_not_break_pairing(self):
        self.controller.settings.save({**self.controller.settings.values, "host": "tv.local"})
        with patch.object(self.controller.tv, "session", self.fake_session(None)):
            result = await self.controller.action("pair")
        self.assertTrue(result["ok"])
        self.assertEqual(self.controller.settings.key, "new-key")
        self.assertEqual(self.controller.settings.values["mac"], "")

    async def test_pairing_keeps_manually_entered_mac(self):
        self.controller.settings.save({**self.controller.settings.values, "host": "tv.local", "mac": "02:00:00:00:00:09"})
        with patch.object(self.controller.tv, "session", self.fake_session()):
            self.assertTrue((await self.controller.action("pair"))["ok"])
        self.assertEqual(self.controller.settings.values["mac"], "02:00:00:00:00:09")

    async def test_paired_tv_works_without_arp(self):
        self.controller.settings.save({**self.controller.settings.values, "host": "tv.local"})
        self.controller.settings.save(key="saved-key", replace_key=True)
        with patch.object(self.controller.tv, "session", self.fake_session()), \
             patch("backend.controller.detect_mac", AsyncMock()) as arp:
            result = await self.controller.detect_tv_mac("tv.local")
        self.assertEqual(result["mac"], "02:00:00:00:00:01")
        self.assertFalse(result["pairing_required"])
        arp.assert_not_awaited()

    async def test_other_host_cannot_reuse_saved_pairing(self):
        self.controller.settings.save({**self.controller.settings.values, "host": "tv.local"})
        self.controller.settings.save(key="saved-key", replace_key=True)
        with patch.object(self.controller.tv, "session") as session, \
             patch("backend.controller.detect_mac", AsyncMock(return_value=None)):
            result = await self.controller.detect_tv_mac("different.local")
        self.assertTrue(result["pairing_required"])
        session.assert_not_called()

    async def test_power_status_is_recently_confirmed_then_expires(self):
        @asynccontextmanager
        async def session(*args, **kwargs):
            yield SimpleNamespace(active_input=AsyncMock(return_value="com.webos.app.hdmi1"),
                                  request=AsyncMock(return_value={"state": "Active"}))
        with patch.object(self.controller.tv, "session", session):
            self.assertTrue((await self.controller.action("test"))["ok"])
        self.assertEqual((await self.controller.get_state())["tv_power"], "on")
        self.controller.power_checked_at -= 16
        self.assertEqual((await self.controller.get_state())["tv_power"], "unknown")

    async def test_wake_does_not_claim_tv_is_on(self):
        import time
        self.controller.power_state = "on"
        self.controller.power_checked_at = time.monotonic()
        self.controller.tv.wake = AsyncMock()
        self.assertTrue((await self.controller.action("wake"))["ok"])
        self.assertEqual((await self.controller.get_state())["tv_power"], "unknown")
