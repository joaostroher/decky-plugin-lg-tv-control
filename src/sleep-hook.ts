export type SleepRegistration = { unregister: () => void } | (() => void);
export type SleepSystem = {
  RegisterForOnSuspendRequest?: (callback: () => void) => SleepRegistration | void | Promise<SleepRegistration | void>;
};
export type SleepHookReason = "registered" | "missing_client" | "missing_event" | "register_failed" | "invalid_handle" | "stopped";

const everyTwoSeconds = (tick: () => void) => {
  const timer = setInterval(tick, 2000);
  return () => clearInterval(timer);
};

// Steam's native bridge and the backend can become ready after plugin loading.
// Retry discovery/reporting without registering duplicate native listeners.
export function registerSleepHook(
  source: SleepSystem | (() => SleepSystem | undefined),
  suspend: () => Promise<unknown>,
  report: (active: boolean, reason: SleepHookReason) => Promise<unknown>,
  schedule = everyTwoSeconds,
) {
  let disposed = false;
  let busy = false;
  let unregister: (() => void) | undefined;
  let attempted: SleepSystem | undefined;
  let eventSeen = false;
  let active = false;
  let reason: SleepHookReason = "missing_client";
  const invoke = (work: () => Promise<unknown>) => {
    try { void work().catch(() => {}); } catch { /* The backend may be unloading. */ }
  };
  const callback = () => {
    if (disposed) return;
    eventSeen = active = true;
    reason = "registered";
    invoke(suspend);
    invoke(() => report(active, reason));
  };
  const tick = async () => {
    if (disposed || busy) return;
    busy = true;
    try {
      if (!active) {
        const system = typeof source === "function" ? source() : source;
        if (!system) reason = "missing_client";
        else if (typeof system.RegisterForOnSuspendRequest !== "function") reason = "missing_event";
        else if (system !== attempted) {
          try {
            const registration = await system.RegisterForOnSuspendRequest(callback);
            const cleanup = typeof registration === "function" ? registration
              : registration && typeof registration.unregister === "function" ? () => registration.unregister() : undefined;
            if (disposed) { cleanup?.(); return; }
            unregister = cleanup;
            attempted = system;
            active = Boolean(cleanup) || eventSeen;
            reason = active ? "registered" : "invalid_handle";
          } catch {
            reason = "register_failed";
          }
        }
      }
      if (!disposed) await report(active, reason);
    } catch {
      // Retry transient backend failures. Until acknowledged, the backend shows
      // that it has received no frontend status, rather than guessing API support.
    } finally { busy = false; }
  };
  const stopTimer = schedule(() => { void tick(); });
  void tick();
  return () => {
    if (disposed) return;
    disposed = true;
    stopTimer();
    try { unregister?.(); } catch { /* Steam may already be shutting down. */ }
    invoke(() => report(false, "stopped"));
  };
}
