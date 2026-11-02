export type SleepRegistration = { unregister: () => void } | (() => void);
export type SleepSystem = {
  RegisterForOnSuspendRequest?: (callback: () => void) => SleepRegistration | void | Promise<SleepRegistration | void>;
  RegisterForPrepareForSystemSuspendProgress?: (callback: (progress: { state?: unknown }) => void) => SleepRegistration | void | Promise<SleepRegistration | void>;
};
export type SleepHookReason = "registered" | "registered_user_progress" | "missing_client" | "missing_event" | "register_failed" | "invalid_handle" | "stopped";
export type SteamHost = {
  SteamClient?: { System?: SleepSystem; User?: SleepSystem };
  SuspendResumeStore?: { suspending?: unknown };
};

export function resolveSleepEvents(hosts: SteamHost[]): SleepSystem | undefined {
  const clients = hosts.map(host => host.SteamClient);
  const legacy = clients.find(client => typeof client?.System?.RegisterForOnSuspendRequest === "function");
  if (legacy) return legacy.System;
  const current = clients.find(client => typeof client?.User?.RegisterForPrepareForSystemSuspendProgress === "function");
  return current?.User ?? clients.find(client => client?.System)?.System;
}

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
  isSuspending: () => boolean | undefined = () => undefined,
) {
  let disposed = false;
  let busy = false;
  let unregister: (() => void) | undefined;
  let attempted: SleepSystem | undefined;
  let eventSeen = false;
  let active = false;
  let userProgress = false;
  let reason: SleepHookReason = "missing_client";
  const invoke = (work: () => Promise<unknown>) => {
    try { void work().catch(() => {}); } catch { /* The backend may be unloading. */ }
  };
  const callback = () => {
    if (disposed) return;
    eventSeen = active = true;
    reason = userProgress ? "registered_user_progress" : "registered";
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
        else if (typeof system.RegisterForOnSuspendRequest !== "function"
                 && typeof system.RegisterForPrepareForSystemSuspendProgress !== "function") reason = "missing_event";
        else if (system !== attempted) {
          try {
            userProgress = typeof system.RegisterForOnSuspendRequest !== "function";
            let armed = false;
            let preparationSeen = false;
            const progressCallback = (progress: { state?: unknown }) => {
              // Ignore initial snapshots delivered while registering. Complete
              // alone may also be a stale snapshot: require Steam's live suspend
              // flag or an observed preparation phase before accepting it.
              if (disposed || !armed || !progress || typeof progress !== "object") return;
              let suspending: boolean | undefined;
              try { suspending = isSuspending(); } catch { /* Unknown remains unverified. */ }
              if (suspending === false) { preparationSeen = false; return; }
              if (progress.state === 2 || progress.state === 4 || progress.state === 5) {
                preparationSeen = true;
              } else if (progress.state === 1) {
                if (suspending === true || preparationSeen) callback();
                preparationSeen = false;
              } else {
                preparationSeen = false;
              }
            };
            const registration = userProgress
              ? await system.RegisterForPrepareForSystemSuspendProgress!(progressCallback)
              : await system.RegisterForOnSuspendRequest!(callback);
            armed = true;
            const cleanup = typeof registration === "function" ? registration
              : registration && typeof registration.unregister === "function" ? () => registration.unregister() : undefined;
            if (disposed) { cleanup?.(); return; }
            unregister = cleanup;
            attempted = system;
            active = Boolean(cleanup) || eventSeen;
            reason = active ? (userProgress ? "registered_user_progress" : "registered") : "invalid_handle";
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
