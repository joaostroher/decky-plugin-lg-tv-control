import { ButtonItem, ConfirmModal, DialogBodyText, DialogButton, DialogControlsSection,
  DialogHeader, DropdownItem, ModalRoot, Navigation, PanelSection, PanelSectionRow,
  QuickAccessTab, TextField, ToggleField, showModal, staticClasses, findSP } from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { FaTv } from "react-icons/fa";
import { registerSleepHook, resolveSleepEvents, type SteamHost, type SleepHookReason } from "./sleep-hook";

type Settings = {
  host: string; mac: string; hdmi: string; broadcast: string; secure: boolean;
  wake_on_resume: boolean; input_on_resume: boolean; off_on_suspend: boolean;
  wake_on_connect: boolean; timeout: number; retries: number; display_id: string; paired: boolean;
};
type State = {
  settings: Settings;
  displays: { connector: string; state: string; identity: string }[];
  display_verified: boolean; lifecycle: string; last_event: string;
  lifecycle_state?: "starting" | "active" | "unavailable" | "disabled";
  tv_power?: "on" | "unknown";
  last_suspend_event?: string;
  suspend_connection?: "ready" | "connecting" | "not_ready";
  steam_suspend_hook?: boolean;
  steam_suspend_hook_detail?: string;
};
type Result = { ok: boolean; message: string };
const getState = callable<[], State>("get_state");
const save = callable<[values: Omit<Settings, "paired">], Result>("save_settings");
const action = callable<[name: string], Result>("action");
const bind = callable<[identity: string], Result>("bind_display");
const discover = callable<[], string[]>("discover_tvs");
const detectMac = callable<[host: string], Result & { mac: string | null; pairing_required: boolean }>("detect_tv_mac");
const retryAutomation = callable<[], Result>("retry_automation");
const steamSuspend = callable<[], Result>("steam_suspend_request");
const reportSleepHook = callable<[active: boolean, reason: SleepHookReason], { ok: boolean }>("set_steam_suspend_hook");

function StatusItem({ label, value, tone, detail }: {
  label: string; value: string; tone: "good" | "warning" | "muted"; detail?: string;
}) {
  const colors = { good: "#a7e7bf", warning: "#ffd28a", muted: "#c3ccd8" };
  return <div style={{ padding: "10px 0", borderBottom: "1px solid rgba(255,255,255,0.07)",
    display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", alignItems: "center", columnGap: 12 }}>
      <dt style={{ fontSize: 13, opacity: 0.8 }}>{label}</dt>
      <dd style={{ margin: 0, padding: "3px 9px", borderRadius: 6, fontSize: 12,
        fontWeight: 600, color: colors[tone], background: "rgba(255,255,255,0.06)", whiteSpace: "nowrap" }}>
        {value}
      </dd>
    {detail && <dd style={{ margin: "7px 0 0", gridColumn: "1 / -1", fontSize: 12, lineHeight: 1.5, opacity: 0.65,
      overflowWrap: "anywhere" }}>{detail}</dd>}
  </div>;
}

function TVStatus({ state, message }: { state: State; message: string }) {
  const lifecycle = state.lifecycle_state ?? "starting";
  const lifecycleLabels = { active: "Active", unavailable: "Unavailable", disabled: "Disabled", starting: "Connecting" };
  return <PanelSection title="Status">
    <PanelSectionRow><div style={{ display: "flex", flexDirection: "column", gap: 12, width: "100%", minWidth: 0 }}>
    <div style={{ width: "100%", minWidth: 0, padding: "2px 14px 14px",
      borderRadius: 10, border: "1px solid rgba(255,255,255,0.08)", background: "rgba(0,0,0,0.14)", boxSizing: "border-box" }}>
      <dl style={{ margin: 0 }}>
        <StatusItem label="Pairing" value={state.settings.paired ? "Paired" : "Not paired"}
          tone={state.settings.paired ? "good" : "muted"} />
        <StatusItem label="TV power" value={state.tv_power === "on" ? "On" : "Unknown"}
          tone={state.tv_power === "on" ? "good" : "muted"}
          detail={state.tv_power === "on" ? "Recently confirmed by the TV." : "Run Test connection to check the TV. A wake packet does not confirm power state."} />
        <StatusItem label="External display" value={state.display_verified ? "Verified" : "Not verified"}
          tone={state.display_verified ? "good" : "warning"}
          detail={state.display_verified ? undefined : "Automatic TV commands require a connected, bound display."} />
        <StatusItem label="Suspend / resume" value={lifecycleLabels[lifecycle]}
          tone={lifecycle === "active" ? "good" : lifecycle === "unavailable" ? "warning" : "muted"}
          detail={state.lifecycle} />
        {lifecycle === "active" && <StatusItem label="Steam sleep event"
          value={state.steam_suspend_hook ? "Registered" : "Unavailable"}
          tone={state.steam_suspend_hook ? "good" : "warning"}
          detail={state.steam_suspend_hook_detail ?? "Waiting for frontend sleep-event status."} />}
        {lifecycle === "active" && state.settings.off_on_suspend && <StatusItem
          label="TV sleep connection" value={state.suspend_connection === "ready" ? "Ready" : state.suspend_connection === "connecting" ? "Connecting" : "Not ready"}
          tone={state.suspend_connection === "ready" ? "good" : "muted"}
          detail={state.suspend_connection === "ready" ? "Authenticated and input query checked in advance." : "Prepared while the bound display is connected and the TV is reachable."} />}
      </dl>
      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", opacity: 0.5 }}>Last activity</div>
        <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.5, overflowWrap: "anywhere" }}>{state.last_event}</div>
      </div>
      {state.last_suspend_event && <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", opacity: 0.5 }}>Last suspend</div>
        <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.5, overflowWrap: "anywhere" }}>{state.last_suspend_event}</div>
      </div>}
    </div>
    {message && <div role="status" style={{ fontSize: 13, lineHeight: 1.5,
      padding: "10px 12px", borderLeft: "3px solid #66c0f4", background: "rgba(102,192,244,0.07)",
      borderRadius: 4, width: "100%", boxSizing: "border-box", overflowWrap: "anywhere" }}>{message}</div>}
    </div></PanelSectionRow>
  </PanelSection>;
}

function SelectTVModal({ hosts, currentHost, onSelect, onClose }: {
  hosts: string[]; currentHost: string; onSelect: (host: string) => void; onClose: () => void;
}) {
  return <ModalRoot onCancel={onClose} closeModal={onClose}>
    <DialogHeader>Select a TV</DialogHeader>
    <DialogBodyText>Choose a discovered TV, then save your settings and pair.</DialogBodyText>
    <DialogControlsSection style={{ maxHeight: "50vh", overflowY: "auto" }}>
      {hosts.map(host => <DialogButton key={host}
        style={{ width: "100%", marginBottom: 8, textAlign: "left" }}
        onClick={() => { onSelect(host); onClose(); }}>
        {host}{host === currentHost ? " · current" : ""}
      </DialogButton>)}
    </DialogControlsSection>
    <DialogButton onClick={onClose}>Cancel</DialogButton>
  </ModalRoot>;
}

function Content() {
  const [state, setState] = useState<State>();
  const [draft, setDraft] = useState<Settings>();
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState("Loading…");
  const [detectingMac, setDetectingMac] = useState(false);
  const [macNeedsPairing, setMacNeedsPairing] = useState(false);
  const macRequest = useRef(0);
  const mounted = useRef(false);
  const activeDialog = useRef<ReturnType<typeof showModal> | null>(null);
  const reopenTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function refresh(reset = false) {
    const next = await getState();
    setState(next);
    setDraft(old => reset || !old ? next.settings : old);
  }
  useEffect(() => {
    mounted.current = true;
    let alive = true;
    getState().then(next => {
      if (alive) { setState(next); setDraft(next.settings); setMessage(""); }
    }).catch(() => { if (alive) setMessage("Backend unavailable. Reload the plugin."); });
    const timer = setInterval(() => {
      getState().then(next => { if (alive) setState(next); }).catch(() => {});
    }, 3000);
    return () => {
      alive = false;
      mounted.current = false;
      clearInterval(timer);
      if (reopenTimer.current !== null) clearTimeout(reopenTimer.current);
      activeDialog.current?.Close();
    };
  }, []);

  async function detectTVMac(host: string, replace = false) {
    const request = ++macRequest.current;
    setDetectingMac(true);
    try {
      const result = await detectMac(host);
      if (!mounted.current || request !== macRequest.current) return;
      setMacNeedsPairing(result.pairing_required);
      if (result.mac) {
        const mac = result.mac;
        setDraft(current => current?.host.trim() === host && (replace || !current.mac)
          ? { ...current, mac } : current);
        setDirty(true);
      }
      setMessage(result.message);
    } catch {
      if (mounted.current && request === macRequest.current)
        setMessage("Could not detect the MAC address. You can enter it manually.");
    } finally {
      if (mounted.current && request === macRequest.current) setDetectingMac(false);
    }
  }

  useEffect(() => {
    ++macRequest.current;
    setDetectingMac(false);
    setMacNeedsPairing(false);
    const host = draft?.host.trim();
    if (!host || draft?.mac) return;
    const timer = setTimeout(() => void detectTVMac(host), 700);
    return () => { clearTimeout(timer); ++macRequest.current; };
  }, [draft?.host]);

  async function run(work: () => Promise<Result>, reset = false) {
    setBusy(true);
    setMessage("Working…");
    try {
      const result = await work();
      setMessage(result.message);
      toaster.toast({ title: result.ok ? "LG TV Control" : "Check your settings", body: result.message });
      await refresh(reset && result.ok);
      if (reset && result.ok) setDirty(false);
    } catch {
      setMessage("Could not communicate with the backend.");
    } finally { setBusy(false); }
  }
  function update<K extends keyof Settings>(key: K, value: Settings[K]) {
    if (key === "host" || key === "mac") {
      ++macRequest.current;
      setDetectingMac(false);
      setMacNeedsPairing(false);
    }
    setDraft(old => old ? { ...old, ...(key === "host" && value !== old.host ? { mac: "" } : {}), [key]: value } : old);
    setDirty(true);
  }
  function showSearchDialog(content: ReactNode) {
    const dialog = showModal(content);
    activeDialog.current = dialog;
    void dialog.ClosedPromise.then(() => {
      if (activeDialog.current === dialog) activeDialog.current = null;
      if (!mounted.current) return;
      // Let Steam finish closing its modal before restoring the quick-access panel.
      reopenTimer.current = setTimeout(() => {
        reopenTimer.current = null;
        if (mounted.current) Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
      }, 250);
    });
    return dialog;
  }
  async function searchTVs() {
    setBusy(true);
    setMessage("Searching for TVs…");
    try {
      const hosts = await discover();
      if (!mounted.current) return;
      if (!hosts.length) {
        const description = "No TVs found. Check that your TV is on and on the same network, or enter its address manually.";
        setMessage(description);
        const dialog = showSearchDialog(<ConfirmModal strTitle="No TVs found"
          strDescription={description} bAlertDialog strOKButtonText="Got it"
          onOK={() => dialog.Close()} onCancel={() => dialog.Close()} />);
        return;
      }
      setMessage("Choose a TV in the dialog.");
      const dialog = showSearchDialog(<SelectTVModal hosts={hosts} currentHost={draft?.host ?? ""}
        onClose={() => {
          setMessage(current => current === "Choose a TV in the dialog." ? "Selection canceled." : current);
          dialog.Close();
        }} onSelect={host => {
          update("host", host);
          setMessage(`TV selected: ${host}. Save your settings to continue.`);
        }} />);
    } catch {
      setMessage("Could not search for TVs. Try again or enter the address manually.");
    } finally { setBusy(false); }
  }
  const button = (label: string, work: () => Promise<Result>, requiresSaved = true, reset = false) => (
    <PanelSectionRow><ButtonItem layout="below" disabled={busy || (requiresSaved && dirty)}
      onClick={() => void run(work, reset)}>{label}</ButtonItem></PanelSectionRow>
  );
  if (!state || !draft) return <PanelSection><PanelSectionRow>{message}</PanelSectionRow></PanelSection>;
  return <>
    <PanelSection title="TV controls">
      {button("Wake TV (Wake-on-LAN)", () => action("wake"))}
      {button(`Select ${state.settings.hdmi.replace("_", " ")}`, () => action("input"))}
      {button("Power off TV", () => action("off"))}
      {button("Test connection", () => action("test"))}
      {button("Pair / authorize TV", () => action("pair"), true, true)}
      {dirty && <PanelSectionRow><div style={{ fontSize: 12, color: "#ffd28a" }}>Save your changes to use the controls.</div></PanelSectionRow>}
    </PanelSection>
    <TVStatus state={state} message={message} />
    <PanelSection title="TV & network">
      <PanelSectionRow><div style={{ display: "flex", flexDirection: "column", gap: 16, width: "100%" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <TextField label="IP address or hostname" value={draft.host}
            disabled={busy} onChange={event => update("host", event.target.value)} />
          <DialogButton style={{ width: "100%" }} disabled={busy}
            onClick={() => void searchTVs()}>Find TVs on the network</DialogButton>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <TextField label="TV MAC address" value={draft.mac} disabled={busy}
            onChange={event => update("mac", event.target.value)} />
          <DialogButton style={{ width: "100%" }} disabled={busy || detectingMac || !draft.host.trim()}
            onClick={() => void detectTVMac(draft.host.trim(), true)}>
            {detectingMac ? "Detecting MAC…" : "Detect MAC from IP"}
          </DialogButton>
          {macNeedsPairing && !draft.mac && <DialogButton style={{ width: "100%" }}
            disabled={busy || detectingMac} onClick={() => void run(async () => {
              const { paired: _paired, ...values } = draft;
              const saved = await save(values);
              if (!saved.ok) return saved;
              return action("pair");
            }, true)}>Save IP and pair to detect MAC</DialogButton>}
        </div>
      </div></PanelSectionRow>
      <PanelSectionRow><DropdownItem label="Deck HDMI input" selectedOption={draft.hdmi}
        disabled={busy} rgOptions={[1, 2, 3, 4].map(n => ({ label: `HDMI ${n}`, data: `HDMI_${n}` }))}
        onChange={option => update("hdmi", option.data)} /></PanelSectionRow>
      <PanelSectionRow><ToggleField label="Use TLS (port 3001)" checked={draft.secure} disabled={busy}
        description="Turn off for older TVs that only support port 3000."
        onChange={value => update("secure", value)} /></PanelSectionRow>
      <PanelSectionRow><TextField label="Wake-on-LAN broadcast" value={draft.broadcast} disabled={busy}
        onChange={event => update("broadcast", event.target.value)} /></PanelSectionRow>
    </PanelSection>
    <PanelSection title="Bind the TV display">
      <PanelSectionRow><div style={{ fontSize: 13 }}>Connect the Deck to your TV, select the Deck HDMI input, then bind the matching connector. Manual controls work without a valid display identity.</div></PanelSectionRow>
      {state.displays.map(display => <PanelSectionRow key={display.connector}>
        <ButtonItem layout="below" disabled={busy || dirty || !display.identity || display.state !== "connected"}
          onClick={() => void run(() => bind(display.identity), true)}>
          {display.connector}: {display.state}{display.identity === state.settings.display_id ? " · bound" : ""}
        </ButtonItem>
      </PanelSectionRow>)}
      {state.displays.length === 0 && <PanelSectionRow>No external display connectors detected.</PanelSectionRow>}
    </PanelSection>
    <PanelSection title="Automation">
      {button("Recheck suspend automation", () => retryAutomation(), false)}
      {([
        ["wake_on_resume", "Wake TV on resume"],
        ["input_on_resume", "Select HDMI on resume"],
        ["off_on_suspend", "Power off TV on suspend"],
        ["wake_on_connect", "Wake / select HDMI on display connection"],
      ] as const).map(([key, label]) => <PanelSectionRow key={key}>
        <ToggleField label={label} checked={draft[key]} disabled={busy} onChange={value => update(key, value)} />
      </PanelSectionRow>)}
      <PanelSectionRow><div style={{ paddingTop: 12, width: "100%" }}>
        <TextField label="Resume timeout (5–90 s)" value={String(draft.timeout)} disabled={busy}
          onChange={event => update("timeout", Number(event.target.value))} />
      </div></PanelSectionRow>
      <PanelSectionRow><TextField label="Retry attempts (1–10)" value={String(draft.retries)} disabled={busy}
        onChange={event => update("retries", Number(event.target.value))} /></PanelSectionRow>
      <PanelSectionRow><ButtonItem layout="below" disabled={busy || detectingMac || !dirty} onClick={() => void run(() => {
        const { paired: _paired, ...values } = draft;
        return save(values);
      }, true)}>Save settings</ButtonItem></PanelSectionRow>
      {button("Forget pairing", () => action("forget"))}
    </PanelSection>
  </>;
}

export default definePlugin(() => {
  const getHosts = (): SteamHost[] => {
    const hosts: SteamHost[] = [globalThis as typeof globalThis & SteamHost];
    try {
      const sp = findSP() as (Window & SteamHost) | undefined;
      if (sp) hosts.push(sp);
    } catch { /* The SP window can be created later. */ }
    return hosts;
  };
  const getSystem = () => resolveSleepEvents(getHosts());
  const isSuspending = () => {
    const states = getHosts().map(host => host.SuspendResumeStore?.suspending);
    return states.includes(true) ? true : states.includes(false) ? false : undefined;
  };
  const disposeSleepHook = registerSleepHook(getSystem, steamSuspend, reportSleepHook, undefined, isSuspending);
  return {
    name: "LG TV Control",
    // Steam hides the QAM when opening a modal; keep the draft and callbacks alive.
    alwaysRender: true,
    titleView: <div className={staticClasses.Title}>LG TV Control</div>,
    content: <Content />,
    icon: <FaTv />,
    onDismount: disposeSleepHook,
  };
});
