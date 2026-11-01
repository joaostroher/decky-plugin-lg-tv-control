"""Serialized power rules with fresh display gates immediately before side effects."""
import asyncio
import time
from .display import scan, Snapshot


def input_matches(app_id, hdmi):
    return app_id == "com.webos.app." + hdmi.lower().replace("_", "")


class Automation:
    def __init__(self, settings, tv, scanner=scan):
        self.settings, self.tv, self.scan = settings, tv, scanner
        self.lock = asyncio.Lock()
        self.snapshot = Snapshot(0, [])
        self.sleeping = False
        self.last_event = "Waiting for events."
        self.resume_task = None
        self.suspend_handled = False
        self.last_suspend_event = ""
        self.suspend_stage = "idle"
        self.logger = None
        self._prepare_task = None
        self._prepared_context = None
        self._prepared_session = None
        self._prepared_config = None
        self._prepared_key = None
        self._last_prepare_attempt = float("-inf")

    def _prepared_matches(self, config):
        socket = getattr(self._prepared_session, "socket", None)
        return (self._prepared_session is not None and self._prepared_config == config
                and self._prepared_key == self.settings.key and not getattr(socket, "closed", False))

    def suspend_connection(self):
        if self._prepared_matches(self.settings.values):
            return "ready"
        if self._prepare_task and not self._prepare_task.done():
            return "connecting"
        return "not_ready"

    def record_suspend(self, message):
        self.last_suspend_event = message
        self.last_event = message
        if self.logger:
            self.logger.info("Suspend automation: %s", message)

    async def _close_prepared(self):
        context = self._prepared_context
        self._prepared_context = self._prepared_session = None
        self._prepared_config = self._prepared_key = None
        if context:
            try:
                async with asyncio.timeout(0.3):
                    await context.__aexit__(None, None, None)
            except Exception:
                pass

    async def _prepare(self, config, key):
        context = None
        entered = False
        try:
            if self.sleeping or not self.gate(config):
                return
            context = self.tv.session(config, key)
            async with asyncio.timeout(3):
                session = await context.__aenter__()
                entered = True
                # Warm up and verify the same read needed at suspend. Never cache
                # its result: the user can change inputs before the sleep event.
                await session.active_input()
            if (not self.sleeping and config == self.settings.values and key == self.settings.key
                    and self.gate(config)):
                self._prepared_context, self._prepared_session = context, session
                self._prepared_config, self._prepared_key = config, key
                entered = False
        except Exception:
            pass
        finally:
            if entered and context:
                try:
                    async with asyncio.timeout(0.3):
                        await context.__aexit__(None, None, None)
                except Exception:
                    pass

    async def _maintain_prepared(self, connected):
        config = self.settings.values.copy()
        eligible = connected and config["off_on_suspend"] and bool(self.settings.key)
        if self._prepared_session and (not eligible or not self._prepared_matches(config)):
            await self._close_prepared()
        if not eligible:
            if self._prepare_task and not self._prepare_task.done():
                self._prepare_task.cancel()
                await asyncio.gather(self._prepare_task, return_exceptions=True)
            return
        if (not self._prepared_session and (not self._prepare_task or self._prepare_task.done())
                and time.monotonic() - self._last_prepare_attempt >= 5):
            self._last_prepare_attempt = time.monotonic()
            self._prepare_task = asyncio.create_task(self._prepare(config, self.settings.key))

    async def close(self):
        if self._prepare_task and not self._prepare_task.done():
            self._prepare_task.cancel()
            await asyncio.gather(self._prepare_task, return_exceptions=True)
        await self._close_prepared()

    def gate(self, config):
        return self.scan().trusted(config["display_id"])

    def cancel_resume(self):
        if self.resume_task and not self.resume_task.done():
            self.resume_task.cancel()

    def start_resume(self):
        self.cancel_resume()
        self.resume_task = asyncio.create_task(self.resume())

    async def resume(self):
        config = self.settings.values.copy()
        try:
            async with self.lock:
                if self.sleeping or not self.gate(config) or not self.settings.key:
                    self.last_event = "Automation skipped: display or pairing not verified."
                    return
                async with asyncio.timeout(config["timeout"]):
                    for attempt in range(config["retries"]):
                        if self.sleeping or not self.gate(config):
                            return
                        try:
                            if config["wake_on_resume"]:
                                await self.tv.wake(config)
                            if not config["input_on_resume"]:
                                self.last_event = "Wake-on-LAN sent."
                                return
                            # Divide the readiness budget among retries; outer timeout is authoritative.
                            async with asyncio.timeout(max(1, config["timeout"] / config["retries"] - 1)):
                                async with self.tv.session(config, self.settings.key) as session:
                                    if not self.sleeping and self.gate(config):
                                        await session.select_input(config["hdmi"])
                                        self.last_event = "HDMI input selected."
                                    return
                        except Exception:
                            # No raw network exception strings: they may contain pairing data.
                            if attempt + 1 < config["retries"]:
                                await asyncio.sleep(1)
                    self.last_event = "TV unavailable; automation stopped."
        except TimeoutError:
            self.last_event = "Automation timed out."

    async def suspend(self):
        self.sleeping = True
        self.cancel_resume()
        if self.suspend_handled:
            return
        self.suspend_handled = True
        config = self.settings.values.copy()
        captured = self.snapshot
        # Cached before PrepareForSleep: no late inference from vanishing DRM state.
        if not config["off_on_suspend"]:
            self.record_suspend("Suspend skipped: power-off on suspend is disabled.")
            await self.close()
            return
        if not self.settings.key:
            self.record_suspend("Suspend skipped: TV is not paired.")
            await self.close()
            return
        if not captured.trusted(config["display_id"]):
            self.record_suspend("Suspend skipped: the pre-suspend display snapshot is missing, stale or unverified.")
            await self.close()
            return
        try:
            async with asyncio.timeout(1.5):
                self.suspend_stage = "waiting for TV controls"
                async with self.lock:
                    if self._prepared_matches(config):
                        await self._suspend_session(self._prepared_session, config, captured)
                    else:
                        self.suspend_stage = "connecting to TV"
                        async with self.tv.session(config, self.settings.key) as session:
                            await self._suspend_session(session, config, captured)
        except TimeoutError:
            self.record_suspend(f"Suspend timed out while {self.suspend_stage}. TV power-off was not confirmed.")
        except Exception as error:
            self.record_suspend(f"Suspend failed while {self.suspend_stage} ({type(error).__name__}). TV power-off was not confirmed.")
        finally:
            await self.close()

    async def _suspend_session(self, session, config, captured):
        self.suspend_stage = "reading the active TV input"
        app = await session.active_input()
        if not input_matches(app, config["hdmi"]):
            self.record_suspend("Suspend skipped: the TV is using another input/app, or its input is unknown.")
            return
        self.suspend_stage = "verifying the external display"
        if not captured.trusted(config["display_id"]) or not self.gate(config):
            self.record_suspend("Suspend skipped: the external display could not be confirmed before power-off.")
            return
        self.suspend_stage = "sending TV power-off"
        await session.power_off()
        self.record_suspend("Power-off command accepted before suspend.")

    async def poll(self):
        previous = None
        stable = 0
        last_trigger = 0
        while True:
            if not self.sleeping:
                self.snapshot = self.scan()
                connected = self.snapshot.trusted(self.settings.values["display_id"])
                await self._maintain_prepared(connected)
                if connected:
                    stable += 1
                else:
                    stable = 0
                    self.cancel_resume()
                # Ignore initial startup; require a disconnected -> stable connected transition.
                if previous is False and stable >= 2:
                    if self.settings.values["wake_on_connect"] and time.monotonic() - last_trigger > 5:
                        self.start_resume()
                        last_trigger = time.monotonic()
                    previous = True
                elif not connected:
                    previous = False
                elif previous is None:
                    previous = True
            await asyncio.sleep(1)
