"""Exercise actual D-Bus transport/FD passing against a private test bus."""
import asyncio
import os
import shutil
import sys
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dbus_next import PropertyAccess
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, signal, dbus_property
from backend.lifecycle import Lifecycle
from scripts.bundle_dependencies import prepare_dbus_next


class LogindService(ServiceInterface):
    def __init__(self):
        super().__init__("org.freedesktop.login1.Manager")
        self.fds = []

    @method()
    def Inhibit(self, what: 's', who: 's', why: 's', mode: 's') -> 'h':
        reader, writer = os.pipe()
        self.fds.extend([reader, writer])
        return reader

    @dbus_property(access=PropertyAccess.READ)
    def InhibitDelayMaxUSec(self) -> 't':
        return 5_000_000

    @signal()
    def PrepareForSleep(self, sleeping) -> 'b':
        return sleeping


@unittest.skipUnless(shutil.which("dbus-daemon"), "dbus-daemon is not installed")
class DBusIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_bus_inhibitor_and_sleep_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            address = "unix:path=" + directory + "/bus"
            daemon = await asyncio.create_subprocess_exec(shutil.which("dbus-daemon"), "--session", "--nofork",
                "--address=" + address, "--print-address", stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            service_bus = None
            runner = None
            lifecycle = None
            service = LogindService()
            try:
                await asyncio.wait_for(daemon.stdout.readline(), 3)
                service_bus = await MessageBus(bus_address=address, negotiate_unix_fd=True).connect()
                service_bus.export("/org/freedesktop/login1", service)
                await service_bus.request_name("org.freedesktop.login1")
                automation = SimpleNamespace(sleeping=False, suspend_handled=False,
                    cancel_resume=lambda: None, start_resume=lambda: None, suspend=AsyncMock())
                lifecycle = Lifecycle(automation)
                with patch.dict(os.environ, {"DBUS_SYSTEM_BUS_ADDRESS": address}):
                    runner = asyncio.create_task(lifecycle.run())
                    async with asyncio.timeout(4):
                        while lifecycle.status.startswith("Connecting"):
                            await asyncio.sleep(0.01)
                    self.assertIn("Active", lifecycle.status)
                    descriptor = lifecycle.fd
                    self.assertIsNotNone(descriptor)
                    os.fstat(descriptor)
                    service.PrepareForSleep(True)
                    async with asyncio.timeout(2):
                        while lifecycle.fd is not None:
                            await asyncio.sleep(0.01)
                    automation.suspend.assert_awaited_once()
                    service.PrepareForSleep(False)
                    async with asyncio.timeout(2):
                        while lifecycle.fd is None:
                            await asyncio.sleep(0.01)
                    self.assertFalse(automation.sleeping)
                # The shipped client must also work without host site-packages
                # or XML, as in Decky's reduced/frozen Python environment.
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
                runner = None
                import dbus_next
                modules = Path(directory) / "py_modules"
                shutil.copytree(Path(dbus_next.__file__).parent, modules / "dbus_next",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                prepare_dbus_next(modules)
                child_code = '''
import asyncio, importlib.abc, os, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
class WithoutXML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "xml" or fullname.startswith("xml."):
            raise ModuleNotFoundError("XML intentionally absent", name=fullname)
sys.meta_path.insert(0, WithoutXML())
from backend.lifecycle import Lifecycle
class Automation:
    sleeping = False
    suspend_handled = False
    calls = 0
    def cancel_resume(self): pass
    def start_resume(self): pass
    async def suspend(self): self.calls += 1
async def main():
    automation = Automation()
    lifecycle = Lifecycle(automation)
    task = asyncio.create_task(lifecycle.run())
    try:
        async with asyncio.timeout(4):
            while lifecycle.state == "starting": await asyncio.sleep(0.01)
        assert lifecycle.state == "active", lifecycle.status
        print("active", flush=True)
        async with asyncio.timeout(3):
            while lifecycle.fd is not None: await asyncio.sleep(0.01)
        assert automation.calls == 1
        print("suspended", flush=True)
        async with asyncio.timeout(3):
            while lifecycle.fd is None: await asyncio.sleep(0.01)
        assert not automation.sleeping
        print("resumed", flush=True)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
asyncio.run(main())
'''
                with patch.dict(os.environ, {"DBUS_SYSTEM_BUS_ADDRESS": address}):
                    child = await asyncio.create_subprocess_exec(sys.executable, "-I", "-S", "-c", child_code,
                        str(modules), str(Path(__file__).resolve().parents[1]),
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    self.assertEqual((await asyncio.wait_for(child.stdout.readline(), 5)).strip(), b"active")
                    service.PrepareForSleep(True)
                    self.assertEqual((await asyncio.wait_for(child.stdout.readline(), 4)).strip(), b"suspended")
                    service.PrepareForSleep(False)
                    self.assertEqual((await asyncio.wait_for(child.stdout.readline(), 4)).strip(), b"resumed")
                    _, error = await asyncio.wait_for(child.communicate(), 4)
                    self.assertEqual(child.returncode, 0, error.decode())
                finally:
                    if child.returncode is None:
                        child.kill()
                        await child.communicate()
            finally:
                if runner:
                    runner.cancel()
                    await asyncio.gather(runner, return_exceptions=True)
                if service_bus:
                    service_bus.disconnect()
                    await service_bus.wait_for_disconnect()
                for descriptor in service.fds:
                    os.close(descriptor)
                if daemon.returncode is None:
                    daemon.terminate()
                await daemon.communicate()
