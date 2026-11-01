"""Shared manual controls for Decky and the local development server."""
import asyncio
import platform
import time
from .settings import validate
from .display import scan
from .tv_control import TV, TVError, discover
from .network import detect_mac
from .automation import Automation
from .lifecycle import Lifecycle


class Controller:
    def _configure(self, settings):
        self.settings = settings
        self.tv = TV()
        self.automation = Automation(settings, self.tv)
        self.lifecycle = Lifecycle(self.automation)
        self.power_state = "unknown"
        self.power_checked_at = 0
        self.steam_suspend_hook = False

    async def get_state(self):
        snapshot = scan()
        return dict(settings=self.settings.public(), displays=snapshot.public(),
                    display_verified=snapshot.trusted(self.settings.values["display_id"]),
                    lifecycle=self.lifecycle.status, lifecycle_state=self.lifecycle.state,
                    tv_power=self.power_state if time.monotonic() - self.power_checked_at < 15 else "unknown",
                    last_event=self.automation.last_event,
                    last_suspend_event=self.automation.last_suspend_event,
                    suspend_connection=self.automation.suspend_connection(),
                    steam_suspend_hook=self.steam_suspend_hook,
                    diagnostics={"python": platform.python_version(), "lifecycle": self.lifecycle.error})

    async def retry_automation(self):
        return {"ok": False, "message": "Suspend/resume automation is available in Decky, not in local mode."}

    async def save_settings(self, values):
        try:
            async with self.automation.lock:
                # Display identity may only be changed by bind_display, never arbitrary RPC data.
                if values.get("display_id", "") != self.settings.values["display_id"]:
                    raise ValueError("Use Bind display to change the display identity.")
                self.settings.save(values)
            return {"ok": True, "message": "Settings saved."}
        except (ValueError, TypeError):
            return {"ok": False, "message": "Invalid settings. Check the address, MAC, timeout and retry count."}

    async def bind_display(self, identity):
        async with self.automation.lock:
            snapshot = scan()
            if not snapshot.trusted(identity):
                return {"ok": False, "message": "The display is disconnected or has no valid EDID."}
            self.settings.save({**self.settings.values, "display_id": identity})
            return {"ok": True, "message": "Display bound. Make sure it is the configured TV."}

    async def discover_tvs(self):
        try:
            return await discover()
        except Exception:
            return []

    async def detect_tv_mac(self, host):
        paired = False
        try:
            host = validate({"host": host})["host"]
            paired = bool(self.settings.key) and host == self.settings.values["host"]
            mac = None
            if paired:
                try:
                    async with asyncio.timeout(3):
                        async with self.automation.lock:
                            async with self.tv.session(self.settings.values.copy(), self.settings.key) as session:
                                mac = await session.mac_address(host)
                except Exception:
                    pass
            if not mac:
                mac = await detect_mac(host) if host else None
        except (ValueError, TypeError):
            mac = None
            host = ""
        message = "MAC address detected. Save your settings." if mac else (
            "MAC address not detected. Turn on the TV on the same local network, or enter the MAC manually.")
        if not mac and not paired and host:
            message = "The network lookup could not find a MAC address. Save the IP and pair to request it directly from the TV."
        return {"ok": bool(mac), "mac": mac, "pairing_required": bool(host) and not mac and not paired,
                "message": message}

    async def action(self, name):
        started = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout(35 if name == "pair" else 8):
                async with self.automation.lock:
                    if self.automation.sleeping:
                        raise TVError("The Deck is suspending.")
                    config = self.settings.values.copy()
                    if name in ("wake", "off", "test"):
                        self.power_state = "unknown"
                        self.power_checked_at = 0
                    if name == "wake":
                        await self.tv.wake(config)
                        return {"ok": True, "message": "Wake-on-LAN sent. The TV power state is still unknown."}
                    if name == "forget":
                        self.settings.save(key=None, replace_key=True)
                        return {"ok": True, "message": "Pairing key removed."}
                    if name not in ("pair", "test", "off", "input"):
                        raise TVError("Invalid action.")
                    async with self.tv.session(config, self.settings.key, pairing=name == "pair") as session:
                        if name == "pair":
                            self.settings.save(key=session.key, replace_key=True)
                            message = "TV paired."
                            # Pairing succeeds even if this model doesn't expose its network information.
                            if not config["mac"]:
                                mac = None
                                try:
                                    # Optional lookup must not turn a late, successful pairing into a timeout.
                                    remaining = 34 - (asyncio.get_running_loop().time() - started)
                                    async with asyncio.timeout(max(0, min(2.5, remaining))):
                                        mac = await session.mac_address(config["host"])
                                except Exception:
                                    pass
                                if mac:
                                    self.settings.save({**self.settings.values, "mac": mac})
                                    message = "TV paired. Its MAC address was retrieved and saved."
                                else:
                                    message = "TV paired. This model did not provide an unambiguous MAC address; IP controls are ready to use."
                        elif name == "input":
                            await session.select_input(config["hdmi"])
                            message = "HDMI input selected."
                        elif name == "off":
                            await session.power_off()
                            message = "Power-off command sent."
                        else:
                            app = await session.active_input()
                            try:
                                power = await session.request("com.webos.service.tvpower/power/getPowerState")
                                state = "on" if power.get("state") == "Active" else "unknown"
                            except TVError:
                                state = "unknown"
                            self.power_state = state
                            self.power_checked_at = time.monotonic()
                            message = f"TV reachable. Power: {state}. App/input: {app or 'unknown'}."
                    return {"ok": True, "message": message}
        except TVError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not confirm the action. TV state is unknown; check the network and pairing."}
