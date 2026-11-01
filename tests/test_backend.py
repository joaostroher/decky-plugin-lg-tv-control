import asyncio
import json
import os
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.automation import Automation, input_matches
from backend.display import Display, Snapshot, scan
from backend.settings import Settings, DEFAULTS, validate
from backend.tv_control import TV, TVError


IDENTITY = "a" * 64


def snapshot(state="connected", age=0, identity=IDENTITY):
    return Snapshot(time.monotonic() - age, [Display("card1-DP-1", state, identity)])


class SettingsTests(unittest.TestCase):
    def test_defaults_and_private_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(directory)
            self.assertTrue(settings.values["wake_on_resume"])
            self.assertTrue(settings.values["off_on_suspend"])
            self.assertFalse(settings.values["wake_on_connect"])
            settings.save({**settings.values, "host": "tv.local", "mac": "AABBCCDDEEFF"})
            settings.save(key="secret", replace_key=True)
            loaded = Settings(directory)
            self.assertEqual(loaded.key, "secret")
            self.assertEqual(loaded.values["mac"], "AA:BB:CC:DD:EE:FF")
            self.assertEqual(os.stat(loaded.path).st_mode & 0o777, 0o600)
            self.assertNotIn("secret", json.dumps(loaded.public()))

    def test_host_change_clears_key_and_display(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(directory)
            settings.save({**settings.values, "host": "tv.local"})
            settings.save({**settings.values, "display_id": IDENTITY}, key="secret", replace_key=True)
            settings.save({**settings.values, "host": "other.local"})
            self.assertIsNone(settings.key)
            self.assertEqual(settings.values["display_id"], "")

    def test_reject_invalid_configuration(self):
        for key, value in [("timeout", 1), ("timeout", True), ("retries", 0),
                           ("host", "https://tv/"), ("mac", "bad"), ("secure", "yes"),
                           ("hdmi", "HDMI_5"), ("display_id", "bad"), ("unknown", 1),
                           ("broadcast", "not-an-ip")]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate({**DEFAULTS, key: value})


class DisplayTests(unittest.TestCase):
    def test_safety_gate(self):
        self.assertTrue(snapshot().trusted(IDENTITY))
        for state, age, identity in [("unknown", 0, IDENTITY), ("disconnected", 0, IDENTITY),
                                     ("connected", 4, IDENTITY), ("connected", -5, IDENTITY),
                                     ("connected", 0, "b" * 64), ("connected", 0, "")]:
            self.assertFalse(snapshot(state, age, identity).trusted(IDENTITY))

    def test_drm_edid_and_internal_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            edid = bytearray(128)
            edid[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
            edid[127] = -sum(edid) % 256
            for name in ["card0-eDP-1", "card1-DP-1", "card1-HDMI-A-1"]:
                connector = root / name
                connector.mkdir()
                (connector / "status").write_text("connected\n")
                (connector / "edid").write_bytes(edid)
            found = scan(root)
            self.assertEqual(len(found.displays), 2)
            self.assertTrue(all(d.identity for d in found.displays))
            (root / "card1-DP-1/edid").write_bytes(b"invalid")
            self.assertEqual(scan(root).displays[0].identity, "")

    def test_missing_edid_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            connector = Path(directory) / "card1-DP-1"
            connector.mkdir()
            (connector / "status").write_text("connected")
            found = scan(directory)
            self.assertFalse(found.trusted(IDENTITY))
            self.assertEqual(found.displays[0].state, "unknown")

    def test_empty_drm_does_not_authorize(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(scan(directory).trusted(IDENTITY))


class FakeTV:
    def __init__(self):
        self.commands = []
        self.app = "com.webos.app.hdmi1"
        self.fail = False
        self.slow = False

    async def wake(self, config):
        self.commands.append("wake")

    @asynccontextmanager
    async def session(self, config, key):
        if self.fail:
            raise OSError("unavailable")
        if self.slow:
            await asyncio.sleep(30)
        yield self

    async def active_input(self):
        return self.app

    async def select_input(self, hdmi):
        self.commands.append(hdmi)

    async def power_off(self):
        self.commands.append("off")


class AutomationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(self.directory.name)
        self.settings.save({**self.settings.values, "host": "tv.local", "retries": 1})
        self.settings.save({**self.settings.values, "display_id": IDENTITY}, key="secret", replace_key=True)
        self.tv = FakeTV()
        self.current = snapshot()
        self.automation = Automation(self.settings, self.tv, lambda: self.current)
        self.automation.snapshot = snapshot()

    async def asyncTearDown(self):
        self.automation.cancel_resume()
        if self.automation.resume_task:
            await asyncio.gather(self.automation.resume_task, return_exceptions=True)
        await self.automation.close()
        self.directory.cleanup()

    async def test_handheld_resume_and_suspend_zero_commands(self):
        self.current = Snapshot(time.monotonic(), [])
        self.automation.snapshot = self.current
        await self.automation.resume()
        await self.automation.suspend()
        self.assertEqual(self.tv.commands, [])

    async def test_docked_resume(self):
        await self.automation.resume()
        self.assertEqual(self.tv.commands, ["wake", "HDMI_1"])

    async def test_docked_suspend(self):
        await self.automation.suspend()
        self.assertEqual(self.tv.commands, ["off"])

    async def test_suspend_reuses_connection_prepared_while_awake(self):
        await self.automation._maintain_prepared(True)
        await self.automation._prepare_task
        self.assertEqual(self.automation.suspend_connection(), "ready")
        self.tv.fail = True  # Opening a new connection during suspend would now fail.
        await self.automation.suspend()
        self.assertEqual(self.tv.commands, ["off"])
        self.assertIn("accepted before suspend", self.automation.last_suspend_event)
        self.assertEqual(self.automation.suspend_connection(), "not_ready")

    async def test_prepared_connection_does_not_override_input_guard(self):
        await self.automation._maintain_prepared(True)
        await self.automation._prepare_task
        self.tv.app = "netflix"
        await self.automation.suspend()
        self.assertEqual(self.tv.commands, [])
        self.assertIn("another input/app", self.automation.last_suspend_event)

    async def test_ready_requires_successful_input_read(self):
        reading = asyncio.Event()
        finish_read = asyncio.Event()

        async def active_input():
            reading.set()
            await finish_read.wait()
            return self.tv.app

        self.tv.active_input = active_input
        await self.automation._maintain_prepared(True)
        await asyncio.wait_for(reading.wait(), 1)
        self.assertEqual(self.automation.suspend_connection(), "connecting")
        finish_read.set()
        await self.automation._prepare_task
        self.assertEqual(self.automation.suspend_connection(), "ready")

    async def test_failed_input_read_does_not_mark_connection_ready(self):
        self.tv.active_input = AsyncMock(side_effect=OSError("unavailable"))
        await self.automation._maintain_prepared(True)
        await self.automation._prepare_task
        self.assertEqual(self.automation.suspend_connection(), "not_ready")
        self.assertEqual(self.tv.commands, [])

    async def test_prepare_never_connects_in_handheld_or_unpaired_mode(self):
        from unittest.mock import MagicMock
        self.tv.session = MagicMock(wraps=self.tv.session)
        await self.automation._maintain_prepared(False)
        self.settings.key = None
        await self.automation._maintain_prepared(True)
        self.tv.session.assert_not_called()

    async def test_settings_change_invalidates_prepared_connection(self):
        await self.automation._maintain_prepared(True)
        await self.automation._prepare_task
        self.settings.values["host"] = "another.local"
        self.assertEqual(self.automation.suspend_connection(), "not_ready")
        self.tv.fail = True
        await self.automation.suspend()
        self.assertEqual(self.tv.commands, [])

    async def test_suspend_result_survives_resume(self):
        self.tv.app = "netflix"
        await self.automation.suspend()
        result = self.automation.last_suspend_event
        self.automation.sleeping = False
        await self.automation.resume()
        self.assertEqual(self.automation.last_suspend_event, result)
        self.assertEqual(self.automation.last_event, "HDMI input selected.")

    async def test_unplug_closes_prepared_connection(self):
        await self.automation._maintain_prepared(True)
        await self.automation._prepare_task
        await self.automation._maintain_prepared(False)
        self.assertEqual(self.automation.suspend_connection(), "not_ready")
        self.assertIsNone(self.automation._prepared_context)

    async def test_other_app_or_unknown_input_never_off(self):
        for app in (None, "com.webos.app.hdmi2", "netflix", "hdmi1", "com.webos.app.hdmi10"):
            self.tv.app = app
            await self.automation.suspend()
        self.assertEqual(self.tv.commands, [])

    async def test_stale_or_unknown_or_wrong_display_no_off(self):
        for cached in (snapshot(age=4), snapshot("unknown"), snapshot(identity="b" * 64)):
            self.automation.snapshot = cached
            await self.automation.suspend()
        self.assertEqual(self.tv.commands, [])

    async def test_unplug_during_input_read_no_off(self):
        async def unplug():
            self.current = snapshot("disconnected")
            return "com.webos.app.hdmi1"
        self.tv.active_input = unplug
        await self.automation.suspend()
        self.assertEqual(self.tv.commands, [])

    async def test_unavailable_tv_is_bounded(self):
        self.tv.fail = True
        await asyncio.wait_for(self.automation.suspend(), 2)
        self.assertEqual(self.tv.commands, [])
        self.automation.sleeping = False
        await asyncio.wait_for(self.automation.resume(), 2)
        self.assertEqual(self.tv.commands, ["wake"])

    async def test_slow_suspend_does_not_hold_deck(self):
        self.tv.slow = True
        started = time.monotonic()
        await self.automation.suspend()
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(self.tv.commands, [])
        self.assertIn("connecting to TV", self.automation.last_suspend_event)

    async def test_cancel_resume_before_suspend(self):
        self.tv.slow = True
        self.automation.start_resume()
        await asyncio.sleep(0.02)
        self.tv.slow = False
        await self.automation.suspend()
        self.assertEqual(self.tv.commands, ["wake", "off"])

    async def test_disabled_options(self):
        self.settings.values.update(wake_on_resume=False, input_on_resume=False, off_on_suspend=False)
        await self.automation.resume()
        await self.automation.suspend()
        self.assertEqual(self.tv.commands, [])

    async def test_no_credentials_no_automatic_commands(self):
        self.settings.key = None
        await self.automation.resume()
        await self.automation.suspend()
        self.assertEqual(self.tv.commands, [])

    async def test_hotplug_stabilizes_without_startup_wake(self):
        self.settings.values["wake_on_connect"] = True
        poll = asyncio.create_task(self.automation.poll())
        try:
            await asyncio.sleep(0.02)
            self.assertEqual(self.tv.commands, [])
            self.current = snapshot("disconnected")
            await asyncio.sleep(1.05)
            self.current = snapshot()
            await asyncio.sleep(2.05)
            self.assertEqual(self.tv.commands, ["wake", "HDMI_1"])
            await asyncio.sleep(1.05)
            self.assertEqual(self.tv.commands, ["wake", "HDMI_1"])
        finally:
            poll.cancel()
            await asyncio.gather(poll, return_exceptions=True)

    def test_exact_app_mapping(self):
        self.assertTrue(input_matches("com.webos.app.hdmi4", "HDMI_4"))
        self.assertFalse(input_matches(None, "HDMI_4"))


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_pair_then_control_over_real_websocket(self):
        from websockets.legacy.server import serve
        from websockets.legacy.client import connect
        received = []

        async def server(ws):
            registration = json.loads(await ws.recv())
            received.append(registration)
            await ws.send(json.dumps({"id": "register", "type": "response", "payload": {"pairingType": "PROMPT"}}))
            await ws.send(json.dumps({"id": "register", "type": "registered", "payload": {"client-key": "secret"}}))
            for _ in range(3):
                command = json.loads(await ws.recv())
                received.append(command)
                await ws.send(json.dumps({"id": command["id"], "type": "response",
                    "payload": {"returnValue": True, "appId": "com.webos.app.hdmi1"}}))

        async with serve(server, "127.0.0.1", 0) as host:
            port = host.sockets[0].getsockname()[1]
            def local_connect(url, **kwargs):
                kwargs.pop("ssl", None)
                return connect(f"ws://127.0.0.1:{port}", **kwargs)
            with patch("websockets.legacy.client.connect", local_connect):
                async with TV().session({**DEFAULTS, "host": "tv.local"}, None, pairing=True) as session:
                    self.assertEqual(session.key, "secret")
                    self.assertEqual(await session.active_input(), "com.webos.app.hdmi1")
                    await session.select_input("HDMI_1")
                    await session.power_off()
        self.assertEqual(received[2]["payload"], {"inputId": "HDMI_1"})
        self.assertEqual(received[3]["uri"], "ssap://system/turnOff")
        self.assertIn("LAUNCH", received[0]["payload"]["manifest"]["permissions"])

    async def test_missing_key_never_connects(self):
        with self.assertRaises(TVError):
            async with TV().session({**DEFAULTS, "host": "tv.local"}, None):
                self.fail("Unexpected session")

    async def test_automation_rejects_pairing_prompt(self):
        ws = AsyncMock()
        ws.recv.return_value = json.dumps({"id": "register", "type": "response", "payload": {"pairingType": "PROMPT"}})
        @asynccontextmanager
        async def connection(*args, **kwargs):
            yield ws
        with patch("websockets.legacy.client.connect", connection), self.assertRaises(TVError):
            async with TV().session({**DEFAULTS, "host": "tv.local"}, "old-key"):
                self.fail("Prompt must not authorize automation")

    async def test_magic_packet(self):
        from unittest.mock import MagicMock
        sock = MagicMock()
        sock.__enter__.return_value = sock
        loop = asyncio.get_running_loop()
        sender = AsyncMock()
        with patch("backend.tv_control.socket.socket", return_value=sock), patch.object(loop, "sock_sendto", sender):
            await TV().wake({**DEFAULTS, "mac": "AA:BB:CC:DD:EE:FF"})
        packet = sender.call_args.args[1]
        self.assertEqual(packet, b"\xff" * 6 + bytes.fromhex("AABBCCDDEEFF") * 16)
        self.assertEqual(sender.call_args.args[2], ("255.255.255.255", 9))


if __name__ == "__main__":
    unittest.main()
