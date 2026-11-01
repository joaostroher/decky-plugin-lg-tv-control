"""logind delay inhibitor: no installed hooks and at most two seconds per suspend."""
import asyncio
import os
import re


class LifecycleError(Exception):
    def __init__(self, message, error_name=None):
        super().__init__(message)
        self.error_name = error_name


def check_reply(reply, description):
    from dbus_next import MessageType
    if reply.message_type == MessageType.ERROR:
        name = getattr(reply, "error_name", None)
        if name in ("org.freedesktop.DBus.Error.AccessDenied", "org.freedesktop.PolicyKit1.Error.NotAuthorized"):
            raise LifecycleError("permission denied by D-Bus/logind", name)
        if name in ("org.freedesktop.DBus.Error.ServiceUnknown", "org.freedesktop.DBus.Error.NameHasNoOwner"):
            raise LifecycleError("systemd-logind service not found", name)
        raise LifecycleError(description, name)
    return reply


class Lifecycle:
    def __init__(self, automation):
        self.automation = automation
        self.bus = None
        self.fd = None
        self.status = "Connecting to systemd-logind…"
        self.events = asyncio.Queue()
        self.owner = None
        self.budget = 2
        self.last_signal = None
        self.stage = "start"
        self.error = None
        self.logger = None
        self.state = "starting"
        self.system_sleeping = False
        self.suspend_task = None
        self.request_watchdog = None
        self.request_timeout = 5

    def _start_suspend(self, source):
        # Steam and logind may announce the same sleep while work is in flight.
        # Both must await the same bounded task before releasing the inhibitor.
        if self.suspend_task is None:
            self.automation.sleeping = True
            self.automation.cancel_resume()
            self.automation.suspend_source = source
            self.automation.last_suspend_event = f"{source}: Suspend request received."
            self.suspend_task = asyncio.create_task(self._perform_suspend())
        return self.suspend_task

    async def _perform_suspend(self):
        try:
            async with asyncio.timeout(self.budget):
                await self.automation.suspend()
        except TimeoutError:
            stage = getattr(self.automation, "suspend_stage", "handling the suspend request")
            source = self.automation.suspend_source
            message = f"{source}: Suspend deadline reached while {stage}; no retry."
            self.automation.last_event = message
            self.automation.last_suspend_event = message

    async def steam_suspend(self):
        if self.state != "active" or self.fd is None:
            return False
        task = self._start_suspend("Steam request")
        if not self.system_sleeping and self.request_watchdog is None:
            self.request_watchdog = asyncio.create_task(self._expire_request())
        # An interrupted frontend RPC must not orphan work. The task has its own
        # deadline and is also cancelled on lifecycle teardown.
        await asyncio.shield(task)
        return True

    async def _expire_request(self):
        await asyncio.sleep(self.request_timeout)
        if not self.system_sleeping:
            await self._reset_suspend()
            self.automation.last_event = "Steam suspend request was not followed by system sleep; controls restored."

    async def _reset_suspend(self):
        current = asyncio.current_task()
        tasks = [task for task in (self.suspend_task, self.request_watchdog)
                 if task and task is not current]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.suspend_task = self.request_watchdog = None
        self.automation.sleeping = False
        self.automation.suspend_handled = False

    async def call(self, message):
        async with asyncio.timeout(3):
            return await self.bus.call(message)

    def release(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    async def inhibit(self):
        from dbus_next import Message, MessageType
        response = check_reply(await self.call(Message(destination="org.freedesktop.login1",
            path="/org/freedesktop/login1", interface="org.freedesktop.login1.Manager",
            member="Inhibit", signature="ssss", body=["sleep", "LG TV Control",
                "Bounded input-aware TV shutdown", "delay"])), "logind rejected the sleep inhibitor")
        if not response.unix_fds:
            raise LifecycleError("inhibitor returned no Unix descriptor; check D-Bus file descriptor passing support")
        self.fd = response.unix_fds[response.body[0]]
        for fd in response.unix_fds:
            if fd != self.fd:
                os.close(fd)

    async def run(self):
        disconnected = event_task = None
        try:
            self.stage = "load dbus-next"
            from dbus_next import BusType, Message, MessageType
            from dbus_next.aio import MessageBus
            self.stage = "connect to the system D-Bus"
            self.bus = MessageBus(bus_type=BusType.SYSTEM, negotiate_unix_fd=True)
            async with asyncio.timeout(3):
                await self.bus.connect()
            self.stage = "find systemd-logind"
            owner = check_reply(await self.call(Message(destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus", interface="org.freedesktop.DBus",
                member="GetNameOwner", signature="s", body=["org.freedesktop.login1"])), "failed to find logind")
            self.owner = owner.body[0]

            def handler(message):
                if (message.message_type == MessageType.SIGNAL and message.sender == self.owner
                        and message.path == "/org/freedesktop/login1"
                        and message.interface == "org.freedesktop.login1.Manager"
                        and message.member == "PrepareForSleep" and message.signature == "b"):
                    sleeping = message.body[0]
                    if self.last_signal == sleeping:
                        return
                    self.last_signal = sleeping
                    self.system_sleeping = sleeping
                    # Cancel wake work synchronously as soon as sleep is announced.
                    if sleeping:
                        self.automation.sleeping = True
                        self.automation.cancel_resume()
                        if self.suspend_task is None:
                            self.automation.last_suspend_event = "systemd-logind: Suspend request received."
                    self.events.put_nowait(sleeping)

            self.bus.add_message_handler(handler)
            self.stage = "subscribe to PrepareForSleep"
            check_reply(await self.call(Message(destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus", interface="org.freedesktop.DBus", member="AddMatch",
                signature="s", body=["type='signal',sender='org.freedesktop.login1',"
                    "interface='org.freedesktop.login1.Manager',member='PrepareForSleep'"])), "event subscription rejected")
            self.stage = "acquire sleep inhibitor"
            await self.inhibit()
            self.stage = "read inhibitor deadline"
            limit = check_reply(await self.call(Message(destination="org.freedesktop.login1",
                path="/org/freedesktop/login1", interface="org.freedesktop.DBus.Properties",
                member="Get", signature="ss", body=["org.freedesktop.login1.Manager", "InhibitDelayMaxUSec"])), "inhibitor deadline unavailable")
            self.budget = max(0, min(2, limit.body[0].value / 1_000_000 - 0.2))
            self.status = "Active (systemd-logind)"
            self.state = "active"
            self.error = None
            self.stage = "wait for sleep events"
            disconnected = asyncio.ensure_future(self.bus.wait_for_disconnect())
            while True:
                event_task = asyncio.create_task(self.events.get())
                done, _ = await asyncio.wait([event_task, disconnected], return_when=asyncio.FIRST_COMPLETED)
                if disconnected in done:
                    raise RuntimeError("D-Bus disconnected")
                sleeping = event_task.result()
                # Coalesce transitions so a delayed resume cannot wake during suspend.
                while not self.events.empty():
                    sleeping = self.events.get_nowait()
                if sleeping:
                    try:
                        if self.fd is not None:
                            await self._start_suspend("systemd-logind")
                    finally:
                        self.release()
                else:
                    self.release()
                    self.stage = "renew inhibitor after resume"
                    await self.inhibit()
                    self.stage = "wait for sleep events"
                    await self._reset_suspend()
                    self.automation.start_resume()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.state = "unavailable"
            if isinstance(error, LifecycleError):
                reason = str(error)
            elif isinstance(error, ModuleNotFoundError):
                missing = error.name or "unknown"
                if not re.fullmatch(r"[A-Za-z0-9_.]+", missing):
                    missing = "unknown"
                if missing == "dbus_next" or missing.startswith("dbus_next."):
                    reason = f"bundled dependency missing: {missing}. Reinstall the complete plugin ZIP"
                else:
                    reason = f"Python runtime module missing: {missing} (required while loading D-Bus support)"
            elif isinstance(error, FileNotFoundError):
                reason = "system D-Bus socket not found"
            elif isinstance(error, PermissionError):
                reason = "permission denied when accessing D-Bus"
            elif isinstance(error, TimeoutError):
                reason = "D-Bus/logind did not respond within 3 seconds"
            elif type(error).__name__ == "AuthError":
                reason = "user authentication rejected by D-Bus"
            else:
                reason = type(error).__name__
            self.error = {"stage": self.stage, "reason": reason, "type": type(error).__name__}
            if isinstance(error, ModuleNotFoundError):
                self.error["module"] = missing
            self.status = f"Unavailable — {self.stage}: {reason}. Suspend/resume automation disabled."
            if self.logger:
                self.logger.error("Lifecycle unavailable: stage=%s reason=%s type=%s", self.stage, reason, type(error).__name__)
        finally:
            await self._reset_suspend()
            for task in (disconnected, event_task):
                if task:
                    task.cancel()
            await asyncio.gather(*(task for task in (disconnected, event_task) if task), return_exceptions=True)
            self.release()
            if self.bus:
                self.bus.disconnect()
