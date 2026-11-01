# LG TV Control for Decky Loader

> **Original development specification.** This document records the initial requirements. See [README.md](../README.md) for the current implementation, installation steps and known limitations. Unchecked items below are requirements, not a report of current test results.

Control compatible LG webOS televisions from the Steam Deck, including optional power and HDMI input automation when the Deck docks, wakes, or suspends. Communication is over the local network; **HDMI-CEC and Home Assistant are not required**.

## Goals

- Provide manual TV power, input, and connection controls from the Decky Loader quick-access menu.
- Automatically wake the TV and select the configured HDMI input when the Steam Deck wakes **only if a valid external display connection is present**.
- Optionally power off the TV before the Deck suspends, **only when the Deck's external display is connected and the configured TV input is active**.
- Work in Gaming Mode without opening the plugin UI and avoid changing SteamOS's read-only system partition.
- Fail safely: never change the TV's power state on uncertain dock/display status.

## Supported environment (initial target)

- Steam Deck / SteamOS, Decky Loader.
- LG televisions supporting webOS network control and network wake.
- USB-C docks with HDMI output.
- TV and Steam Deck on the same reachable LAN (subject to router isolation and wake capabilities).

**Limitations:** Network wake can depend on TV model, settings (e.g. *Turn on via Wi-Fi*), firmware, power state and network topology. Detecting a physically connected external monitor may differ between docks, especially when the TV is off. All automation behavior must be validated on actual hardware.

## MVP requirements

### Manual control

- [ ] Discover compatible LG TVs on the LAN when possible; allow manual entry.
- [ ] Pair/authorize control with the TV through its webOS prompt.
- [ ] Wake the TV using Wake-on-LAN / supported wake method.
- [ ] Power off using webOS network control.
- [ ] Show TV availability, with a clear `unknown` state when power state cannot be determined reliably.
- [ ] Select the configured HDMI input.
- [ ] Provide `Wake`, `Power off`, `Select input`, and `Test connection` actions in the Decky panel.

### Configuration

- [ ] TV IP / hostname, MAC address, HDMI input, and pairing credentials.
- [ ] Toggle **wake TV on Deck resume** (default on).
- [ ] Toggle **select HDMI on resume** (default on).
- [ ] Toggle **power off TV on Deck suspend** (default on; can be disabled).
- [ ] Toggle **wake on external-display connection** (default off).
- [ ] Configurable connection/readiness timeout and retry policy.
- [ ] Persist preferences across Decky restarts and Deck reboots.
- [ ] Never embed real IPs, MACs, or credentials in source control.

### Mandatory external-display safety gate

- [ ] **No automatic power-on, power-off, or HDMI switching when no external display connection is detected.**
- [ ] Distinguish handheld-only operation from a connected external display.
- [ ] Treat `unknown` connector status as **do nothing**, not `connected`.
- [ ] Gate the suspend action using a **recent, trustworthy state captured before suspend**, because DRM/network access may become unavailable during suspend.
- [ ] Power off on suspend only if the currently selected TV input matches the configured Deck HDMI input; on failure to read the active input, leave the TV on.
- [ ] Manual controls remain usable without an external display connection.

**Implementation note:** Enumerate DRM/KMS connectors via `/sys/class/drm/*/status` and/or appropriate udev/DRM events. USB-C docks can expose the display as DisplayPort rather than HDMI; do not filter solely on connector name. A dock or TV may report `connected` even when the television is powered off. Verify the specific port/monitor identity where possible and account for hotplug races.

## Expected automation behavior

| Situation | Expected behavior |
| --- | --- |
| Deck resumes with an external display connected | Wake TV; wait for network availability; select configured HDMI |
| Deck resumes in handheld mode | No automatic TV commands |
| Deck suspends with a connected display and TV using Deck HDMI input | Optionally power off TV before network sleeps |
| Deck suspends in handheld mode | No automatic TV commands |
| TV is on another HDMI input or app | Do not power off TV |
| Connector state is unknown or stale | No automatic commands |
| External display is connected while Deck is running | Optional wake/switch, off by default |
| Dock unplugged | Do not power off by default |
| TV/network unavailable | Time out, log a non-sensitive error, never block Deck operation |
| User presses a manual control button | Attempt action regardless of external connector status |

## Proposed architecture

```text
Decky quick-access panel (React + TypeScript, @decky/ui)
                  |
                  v
Decky Python backend (settings, pairing, status, command orchestration)
           |                                  |
           v                                  v
LG webOS / Wake-on-LAN adapter          Dock/display detection
(via evaluated lgctl integration        (DRM/KMS + udev; cached
 or native Python library)               state before suspend)
           ^                                  |
           +---------------+------------------+
                           |
                  Lifecycle integration
            (validated SteamOS suspend/resume
             hooks or compatible service)
```

### Design decisions requiring validation

1. **Control library:** Evaluate [lgctl](https://github.com/lmrisdal/lgctl) as an implementation reference or optional helper executable. Check its API/CLI behavior, packaging, license, supported architecture, maintenance and error handling before embedding or distributing it. A native Python webOS library is an alternative.
2. **Lifecycle:** Verify actual Decky lifecycle APIs and/or systemd sleep hooks on the supported SteamOS release. Hooks must fire reliably in Gaming Mode. Suspend actions require a tight deadline and must happen before networking shuts down; never hold up system suspend indefinitely.
3. **Permissions:** Prefer per-user integration where feasible. If privileged system hooks are indispensable, require a narrowly scoped, auditable install/removal mechanism; do not disable read-only root or silently install root services.
4. **Discovery and identity:** Avoid confusing any external monitor with the user's configured LG TV. Consider EDID/connector identity and the configured TV endpoint, with fallbacks for docks whose EDID is unavailable when the TV is off.
5. **Network:** Prefer explicit retries and bounded timeouts over fixed delays. Wake-on-LAN broadcast must work on the local subnet; status probing is not always proof that a TV is off.

## Suggested repository layout

```text
lg-tv-control/
├── README.md
├── plugin.json
├── package.json
├── src/                 # Decky UI (React/TypeScript)
├── main.py              # Decky backend entry point
├── backend/
│   ├── tv_control.py    # webOS + Wake-on-LAN abstraction
│   ├── display.py       # DRM connector discovery/state
│   ├── automation.py    # Rules and idempotent actions
│   └── settings.py      # Configuration/secure credentials
├── scripts/             # Optional, explicit lifecycle setup
└── tests/
```

Start from the [official Decky plugin template](https://github.com/SteamDeckHomebrew/decky-plugin-template) and adapt this structure to current template conventions.

## Implementation phases

1. **Proof of concept:** Pair with LG webOS; manual wake, power off, and HDMI switch; document TV configuration prerequisites.
2. **Decky UI:** Settings, status, test connection, manual commands; persistent and safe credential storage.
3. **Display gate:** Handle DRM connector detection, dock connect/disconnect, stale/unknown status and display identity.
4. **Lifecycle:** Reliable resume/suspend automation using a verified mechanism, bounded execution and pre-suspend state capture.
5. **Packaging and testing:** Reboots, Gaming/Desktop modes, TV off/on, dock changes, networking failure, upgrades and clean uninstall.

## Acceptance tests

- [ ] Handheld resume/suspend causes **zero** TV control commands.
- [ ] Docked resume wakes the TV and selects the designated HDMI (when TV/network supports wake).
- [ ] Docked suspend powers off only when the active TV input matches the configured input.
- [ ] If input or connector status cannot be verified, no automatic power-off occurs.
- [ ] HDMI/DisplayPort connector naming does not break supported docks.
- [ ] Rapid repeated wake/suspend or reconnect events do not cause conflicting commands.
- [ ] An unavailable TV does not hang or crash the Deck or interfere with suspend.
- [ ] Configuration survives restart and works in Gaming Mode without opening the panel.
- [ ] Uninstall removes any optional hooks/services and does not expose credentials.

## Non-goals for MVP

- HDMI-CEC support or additional USB-CEC hardware.
- Home Assistant integration.
- Multi-TV control, volume buttons, platform-independent support, and non-LG TVs.

## Future ideas

Multi-TV profiles, volume control, TV notifications, dock-specific profiles, Bazzite support, better input auto-detection, and optional power-on after connecting a dock while the Deck is already running.

## References

- [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader)
- [Decky official plugin template](https://github.com/SteamDeckHomebrew/decky-plugin-template)
- [lgctl: LG webOS TV control for Linux/SteamOS](https://github.com/lmrisdal/lgctl)

## Project status

This specification describes the original planned behavior. See [README.md](../README.md) for the current project status and hardware validation checklist.
