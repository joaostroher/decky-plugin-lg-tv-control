import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager
import time

from backend.lifecycle import Lifecycle
from backend.automation import Automation
from backend.display import Display, Snapshot
from backend.settings import DEFAULTS


class FakeBus:
    def __init__(self):
        from dbus_next import MessageType
        self.reply = MessageType.METHOD_RETURN
        self.handler = None
        self.inhibitors = []
        self.writers = []
        self.disconnected = asyncio.get_running_loop().create_future()
        self.errors = {}

    async def connect(self):
        return self

    async def call(self, message):
        from dbus_next import Variant
        result = SimpleNamespace(message_type=self.reply, body=[], unix_fds=[])
        if message.member in self.errors:
            from dbus_next import MessageType
            return SimpleNamespace(message_type=MessageType.ERROR, error_name=self.errors[message.member], body=[], unix_fds=[])
        if message.member == "GetNameOwner":
            result.body = [":1.42"]
        elif message.member == "Inhibit":
            reader, writer = os.pipe()
            self.inhibitors.append(reader)
            self.writers.append(writer)
            result.body = [0]
            result.unix_fds = [reader]
        elif message.member == "Get":
            result.body = [Variant("t", 5_000_000)]
        return result

    def add_message_handler(self, handler):
        self.handler = handler

    async def wait_for_disconnect(self):
        await self.disconnected

    def disconnect(self):
        if not self.disconnected.done():
            self.disconnected.set_result(None)

    def signal(self, sleeping, sender=":1.42"):
        from dbus_next import Message, MessageType
        self.handler(Message(message_type=MessageType.SIGNAL, sender=sender,
            path="/org/freedesktop/login1", interface="org.freedesktop.login1.Manager",
            member="PrepareForSleep", signature="b", body=[sleeping]))

    def cleanup(self):
        for fd in self.writers:
            os.close(fd)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.automation = SimpleNamespace(sleeping=False, suspend_handled=False,
            cancel_resume=lambda: None, start_resume=lambda: None, suspend=AsyncMock())
        self.lifecycle = Lifecycle(self.automation)
        self.bus = FakeBus()
        self.patcher = patch("dbus_next.aio.MessageBus", return_value=self.bus)
        self.factory = self.patcher.start()
        self.task = asyncio.create_task(self.lifecycle.run())
        await asyncio.sleep(0.02)

    async def asyncTearDown(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.patcher.stop()
        self.bus.cleanup()

    async def test_suspend_releases_inhibitor_and_resume_reacquires(self):
        self.assertIn("Active", self.lifecycle.status)
        descriptor = self.lifecycle.fd
        self.bus.signal(True)
        await asyncio.sleep(0.02)
        self.automation.suspend.assert_awaited_once()
        self.assertIsNone(self.lifecycle.fd)
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.bus.signal(False)
        await asyncio.sleep(0.02)
        self.assertIsNotNone(self.lifecycle.fd)
        self.assertFalse(self.automation.sleeping)

    async def test_duplicate_signals_only_suspend_once(self):
        self.bus.signal(True)
        self.bus.signal(True)
        await asyncio.sleep(0.02)
        self.automation.suspend.assert_awaited_once()

    async def test_foreign_sender_cannot_trigger(self):
        self.bus.signal(True, sender=":1.99")
        await asyncio.sleep(0.02)
        self.automation.suspend.assert_not_awaited()

    async def test_disconnect_disables_lifecycle_and_releases_fd(self):
        self.bus.disconnect()
        await asyncio.sleep(0.02)
        self.assertIn("Unavailable", self.lifecycle.status)
        self.assertIsNone(self.lifecycle.fd)

    async def test_unload_releases_inhibitor(self):
        descriptor = self.lifecycle.fd
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.assertIsNone(self.lifecycle.fd)
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    async def test_rapid_events_coalesce_to_suspend(self):
        self.bus.signal(True)
        self.bus.signal(False)
        self.bus.signal(True)
        await asyncio.sleep(0.02)
        self.assertTrue(self.automation.sleeping)
        self.automation.suspend.assert_awaited_once()

    async def test_deadline_releases_inhibitor(self):
        self.lifecycle.budget = 0.02
        async def slow():
            await asyncio.sleep(10)
        self.automation.suspend.side_effect = slow
        self.bus.signal(True)
        await asyncio.sleep(0.05)
        self.assertIsNone(self.lifecycle.fd)
        self.assertIn("Active", self.lifecycle.status)

    async def test_steam_and_logind_share_work_and_hold_inhibitor_until_done(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def slow():
            started.set()
            await finish.wait()
            self.automation.last_suspend_event = "Steam request: command accepted."

        self.automation.suspend.side_effect = slow
        request = asyncio.create_task(self.lifecycle.steam_suspend())
        await asyncio.wait_for(started.wait(), 1)
        self.bus.signal(True)
        await asyncio.sleep(0.02)
        self.assertIsNotNone(self.lifecycle.fd)
        self.automation.suspend.assert_awaited_once()
        finish.set()
        self.assertTrue(await request)
        await asyncio.sleep(0.02)
        self.assertIsNone(self.lifecycle.fd)
        self.assertEqual(self.automation.last_suspend_event, "Steam request: command accepted.")

    async def test_cancelled_frontend_rpc_does_not_orphan_suspend_task(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def slow():
            started.set()
            await finish.wait()

        self.automation.suspend.side_effect = slow
        request = asyncio.create_task(self.lifecycle.steam_suspend())
        await asyncio.wait_for(started.wait(), 1)
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        self.assertFalse(self.lifecycle.suspend_task.done())
        self.bus.signal(True)
        finish.set()
        await asyncio.sleep(0.02)
        self.assertIsNone(self.lifecycle.fd)
        self.automation.suspend.assert_awaited_once()

    async def test_unconfirmed_steam_request_expires_and_restores_controls(self):
        self.lifecycle.request_timeout = 0.03
        await self.lifecycle.steam_suspend()
        self.assertTrue(self.automation.sleeping)
        await asyncio.sleep(0.05)
        self.assertFalse(self.automation.sleeping)
        self.assertIsNone(self.lifecycle.suspend_task)
        self.assertIsNotNone(self.lifecycle.fd)
        self.assertIn("controls restored", self.automation.last_event)
        await self.lifecycle.steam_suspend()
        self.assertEqual(self.automation.suspend.await_count, 2)

    async def test_confirmed_sleep_does_not_expire_before_resume(self):
        self.lifecycle.request_timeout = 0.03
        await self.lifecycle.steam_suspend()
        self.bus.signal(True)
        await asyncio.sleep(0.05)
        self.assertTrue(self.automation.sleeping)
        self.bus.signal(False)
        await asyncio.sleep(0.02)
        self.assertFalse(self.automation.sleeping)
        self.assertIsNone(self.lifecycle.suspend_task)
        self.assertIsNone(self.lifecycle.request_watchdog)

    async def test_steam_request_requires_active_inhibitor(self):
        self.lifecycle.release()
        self.assertFalse(await self.lifecycle.steam_suspend())
        self.automation.suspend.assert_not_awaited()

    def install_real_automation(self, app="com.webos.app.hdmi1"):
        tv = SimpleNamespace(active_input=AsyncMock(return_value=app), power_off=AsyncMock())

        @asynccontextmanager
        async def session(*_args):
            yield tv

        tv.session = session
        settings = SimpleNamespace(values={**DEFAULTS, "host": "tv.local", "display_id": "test-display"}, key="test-key")
        snapshot = lambda: Snapshot(time.monotonic(), [Display("card0-HDMI-A-1", "connected", "test-display")])
        automation = Automation(settings, tv, snapshot)
        automation.snapshot = snapshot()
        self.lifecycle.automation = self.automation = automation
        return tv

    async def test_early_request_finishes_before_network_stops_on_logind_event(self):
        tv = self.install_real_automation()
        await self.automation._maintain_prepared(True)
        await self.automation._prepare_task
        self.assertTrue(await self.lifecycle.steam_suspend())
        tv.power_off.assert_awaited_once()
        reads = tv.active_input.await_count
        # Model the reported failure: no request can complete after PrepareForSleep.
        tv.active_input.side_effect = TimeoutError
        self.bus.signal(True)
        await asyncio.sleep(0.02)
        self.assertIsNone(self.lifecycle.fd)
        self.assertEqual(tv.active_input.await_count, reads)
        tv.power_off.assert_awaited_once()
        self.assertIn("Steam request: Power-off command accepted", self.automation.last_suspend_event)

    async def test_early_request_still_reads_current_input_after_preparation(self):
        tv = self.install_real_automation()
        await self.automation._maintain_prepared(True)
        await self.automation._prepare_task
        tv.active_input.return_value = "netflix"
        await self.lifecycle.steam_suspend()
        tv.power_off.assert_not_awaited()
        self.assertIn("another input/app", self.automation.last_suspend_event)

    async def test_permission_denied_diagnoses_the_failed_stage(self):
        self.bus.signal(True)
        await asyncio.sleep(0.02)
        self.bus.errors["Inhibit"] = "org.freedesktop.DBus.Error.AccessDenied"
        self.bus.signal(False)
        await asyncio.sleep(0.02)
        self.assertIn("renew inhibitor", self.lifecycle.status)
        self.assertIn("permission denied", self.lifecycle.status)
        self.assertIsNone(self.lifecycle.fd)
        self.assertEqual(self.lifecycle.error["type"], "LifecycleError")

    async def test_missing_system_bus_has_actionable_diagnostic(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.factory.side_effect = FileNotFoundError("private/path/should-not-be-exposed")
        lifecycle = Lifecycle(self.automation)
        await lifecycle.run()
        self.assertIn("system D-Bus socket not found", lifecycle.status)
        self.assertNotIn("private/path", lifecycle.status)

    async def test_missing_transitive_module_is_not_mislabeled_as_dbus_next(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.factory.side_effect = ModuleNotFoundError("private contents", name="xml")
        lifecycle = Lifecycle(self.automation)
        await lifecycle.run()
        self.assertEqual(lifecycle.error["module"], "xml")
        self.assertIn("Python runtime module missing: xml", lifecycle.status)
        self.assertNotIn("bundled dependency missing", lifecycle.status)
        self.assertNotIn("private contents", lifecycle.status)


if __name__ == "__main__":
    unittest.main()
