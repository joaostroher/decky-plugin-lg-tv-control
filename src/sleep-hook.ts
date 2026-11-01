export type SleepSystem = {
  RegisterForOnSuspendRequest?: (callback: () => void) => { unregister: () => void };
};

// Registered for the plugin lifetime, independently of the quick-access panel.
export function registerSleepHook(
  system: SleepSystem,
  suspend: () => Promise<unknown>,
  report: (active: boolean) => Promise<unknown>,
) {
  let disposed = false;
  let registration: { unregister: () => void } | undefined;
  const invoke = (work: () => Promise<unknown>) => {
    try { void work().catch(() => {}); } catch { /* The backend may be unloading. */ }
  };
  try {
    registration = system.RegisterForOnSuspendRequest?.(() => {
      if (!disposed) invoke(suspend);
    });
  } catch { /* Unsupported Steam clients keep the logind fallback. */ }
  invoke(() => report(Boolean(registration)));
  return () => {
    if (disposed) return;
    disposed = true;
    try { registration?.unregister(); } catch { /* Steam may already be shutting down. */ }
    invoke(() => report(false));
  };
}
