"""Decky API v1 entry point."""
import asyncio
from pathlib import Path
import sys

import decky

PLUGIN_ROOT = Path(__file__).resolve().parent
# Resolve from this entry point, not the loader's current working directory or
# an environment variable whose folder name may differ from our ZIP root.
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT / "py_modules"))
from backend.settings import Settings
from backend.controller import Controller
from backend.lifecycle import Lifecycle


class Plugin(Controller):
    async def set_steam_suspend_hook(self, enabled, reason="registered"):
        details = {
            "registered": "Steam sleep callback registered. Power-off still requires live input and display checks.",
            "missing_client": "Steam System API is not available in the loader or SP window. Retrying every two seconds.",
            "missing_event": "Steam System API is present, but RegisterForOnSuspendRequest is missing. Retrying every two seconds.",
            "register_failed": "Steam sleep-event registration failed. Retrying every two seconds.",
            "invalid_handle": "Steam returned no supported registration handle. Waiting for an event to confirm the listener; not registering duplicates.",
            "stopped": "Frontend sleep listener was unloaded.",
        }
        if type(enabled) is not bool or not isinstance(reason, str) or reason not in details:
            return {"ok": False}
        self.steam_suspend_hook = enabled is True
        detail = details[reason]
        if self.steam_suspend_hook_detail != detail:
            decky.logger.info("Steam sleep listener: %s", reason)
        self.steam_suspend_hook_detail = detail
        return {"ok": True}

    async def steam_suspend_request(self):
        handled = await self.lifecycle.steam_suspend()
        return {"ok": handled, "message": self.automation.last_suspend_event if handled
                else "Steam sleep request ignored: suspend integration is not ready."}

    async def _main(self):
        self._configure(Settings(decky.DECKY_PLUGIN_SETTINGS_DIR))
        self.lifecycle.logger = decky.logger
        self.automation.logger = decky.logger
        self.tasks = [asyncio.create_task(self.automation.poll()),
                      asyncio.create_task(self.lifecycle.run())]
        decky.logger.info("LG TV Control started")

    async def _unload(self):
        self.automation.cancel_resume()
        tasks = self.tasks + ([self.automation.resume_task] if self.automation.resume_task else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.automation.close()

    async def retry_automation(self):
        if self.automation.sleeping:
            return {"ok": False, "message": "Wait for the Deck to resume before checking automation."}
        self.tasks[1].cancel()
        await asyncio.gather(self.tasks[1], return_exceptions=True)
        self.lifecycle = Lifecycle(self.automation)
        self.lifecycle.logger = decky.logger
        self.tasks[1] = asyncio.create_task(self.lifecycle.run())
        return {"ok": True, "message": "Automation check restarted. See its status in the panel."}

    async def _uninstall(self):
        # No external hooks or services. Remove pairing credentials as well.
        self.settings.path.unlink(missing_ok=True)
