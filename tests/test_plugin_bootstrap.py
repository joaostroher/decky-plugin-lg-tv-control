import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.bundle_dependencies import prepare_dbus_next


class PluginBootstrapTests(unittest.TestCase):
    def test_renamed_install_uses_its_own_bundled_modules(self):
        import dbus_next
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            plugin = Path(directory) / "LG TV Control"
            plugin.mkdir()
            shutil.copy(source / "main.py", plugin / "main.py")
            shutil.copytree(source / "backend", plugin / "backend", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            modules = plugin / "py_modules"
            shutil.copytree(Path(dbus_next.__file__).parent, modules / "dbus_next",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            prepare_dbus_next(modules)
            code = '''
import asyncio, importlib.abc, importlib.util, logging, os, pathlib, sys, types
root = pathlib.Path(sys.argv[1]).resolve()
class WithoutXML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "xml" or fullname.startswith("xml."):
            raise ModuleNotFoundError("XML intentionally absent", name=fullname)
sys.meta_path.insert(0, WithoutXML())
sys.modules["decky"] = types.SimpleNamespace(DECKY_PLUGIN_SETTINGS_DIR=str(root / "settings"), logger=logging.getLogger("bootstrap"))
os.environ["DECKY_PLUGIN_DIR"] = "/not/the/installed/plugin"
os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = "unix:path=" + str(root / "missing-test-bus")
spec = importlib.util.spec_from_file_location("plugin_under_test", root / "main.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
async def check():
    plugin = module.Plugin()
    await plugin._main()
    try:
        async with asyncio.timeout(4):
            while plugin.lifecycle.state == "starting": await asyncio.sleep(0.01)
        assert plugin.lifecycle.stage == "connect to the system D-Bus", plugin.lifecycle.status
        import dbus_next
        assert pathlib.Path(dbus_next.__file__).is_relative_to(root / "py_modules")
        assert "module missing" not in plugin.lifecycle.status
        assert (await plugin.set_steam_suspend_hook(False, "missing_event"))["ok"]
        state = await plugin.get_state()
        assert not state["steam_suspend_hook"]
        assert "Neither System.RegisterForOnSuspendRequest" in state["steam_suspend_hook_detail"]
        assert not (await plugin.set_steam_suspend_hook(False, "private arbitrary content"))["ok"]
        assert "private arbitrary content" not in str(await plugin.get_state())
        assert (await plugin.set_steam_suspend_hook(True, "registered"))["ok"]
        assert (await plugin.get_state())["steam_suspend_hook"]
        assert (await plugin.set_steam_suspend_hook(True, "registered_user_progress"))["ok"]
        assert "User suspend preparation" in (await plugin.get_state())["steam_suspend_hook_detail"]
    finally:
        await plugin._unload()
asyncio.run(check())
'''
            result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(plugin)],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
