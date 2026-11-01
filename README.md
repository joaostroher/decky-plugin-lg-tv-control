# LG TV Control for Decky Loader

Control an LG webOS TV from the Steam Deck quick-access menu: discover, pair, wake, power off and select HDMI inputs. Optional suspend/resume automation requires a verified external display.

**100% vibe coded.** This project was built entirely through AI-assisted coding, with human direction and hardware testing.

**Version 0.1.9 — experimental.** Manual wake, power-off and HDMI 1 selection have been confirmed on a real LG TV during development. Suspend/resume automation on a physical Deck is still under investigation. The original requirements and hardware acceptance checklist remain in [docs/SPECIFICATION.md](docs/SPECIFICATION.md).

## Install and configure

1. Install Decky Loader, then install `out/lg-tv-control-v0.1.9.zip` through **Decky settings → Developer → Install Plugin from ZIP File**. The ZIP includes Python dependencies; do not run pip on the Deck.
2. Alternatively, extract its single `lg-tv-control` folder into `~/homebrew/plugins/` in Desktop Mode and reload Decky. Avoid nesting it inside another folder with the same name.
3. Enable network control/LG Connect Apps and network wake on the TV. Settings such as **TV On With Mobile** or **Turn on via Wi-Fi** vary by model. Put the TV and Deck on a reachable LAN without client isolation.
4. Open **LG TV Control → Find TVs on the network**, then select an address in the list. Cancel leaves the current address unchanged. Closing the dialog restores the Decky panel and preserves your fields. You can also enter an IPv4 address or hostname manually.
5. Check the detected MAC address, select the Deck HDMI input and **Save settings**. Use the MAC for the TV's active network interface. A DHCP reservation helps keep the IP stable.
6. With the TV on, choose **Pair / authorize TV** and accept the on-screen request within 35 seconds. Use **Test connection** and the manual controls to check access.
7. Connect the Deck to the TV, select the Deck input on the TV and choose the matching connector under **Bind the TV display**. Confirm that this display belongs to the configured TV.
8. Before testing automation, check that **External display** shows **Verified**, **Suspend / resume** shows **Active**, and **TV sleep connection** shows **Ready** while the TV is reachable.

Actions appear first in the panel, followed by a separate **Status** section. It shows pairing, recently checked TV power, external display identity, suspend/resume integration and the last automation event. A confirmed power reading expires after 15 seconds. An unanswered request or a wake packet never proves that the TV is on or off.

Manual controls do not require an external display. The MAC address is only required for Wake-on-LAN; pairing, HDMI selection and power-off use the TV's IP/hostname.

## Automatic MAC detection

Changing or selecting the TV address triggers a lookup when the MAC field is empty. **Detect MAC from IP** retries it or explicitly replaces an existing MAC. Automatic lookup preserves a manually entered MAC. Changing the TV address clears the previous MAC to avoid waking another device.

Paired TVs are queried directly through webOS before falling back to ARP. If ARP cannot find a MAC, choose **Save IP and pair to detect MAC** and accept the request on the TV. A successful pairing saves the MAC if the TV exposes an unambiguous address. An ordinary lookup fills the form and requires **Save settings** to persist it. Older pairing keys may need renewed authorization for `READ_NETWORK_STATE`.

Direct lookup uses `com.webos.service.connectionmanager/getinfo`. If Wi-Fi and Ethernet report different MACs, it matches the active IP using `getStatus` or the older `getstatus` endpoint. It does not arbitrarily choose between two interfaces. Endpoint availability and permission requirements depend on model/firmware. See the [webOS connection API](https://www.webosose.org/docs/reference/ls2-api/com-webos-service-connectionmanager/).

On SteamOS/Linux, ARP lookup uses `ip -j route get` and `ip -j neigh show`. On macOS, it uses `/sbin/route -n get` and `/usr/sbin/arp`, normalizing octets printed without leading zeroes. No root access is required. A short TCP connection attempt to port 3001 triggers normal address resolution; it sends no webOS or wake command. Routed destinations, incomplete entries and ambiguous results are rejected. ARP lookup has a four-second deadline; a paired-TV query adds up to three seconds.

Detection works best with the TV on and on the same local link. Wi-Fi isolation, VLANs, proxy ARP, multiple DNS addresses, missing tools or stale neighbour entries can prevent accurate results. In this macOS development environment, Python can receive an empty ARP table even while TCP works. Pair and query the TV directly, or enter the MAC shown by `arp -n TV_IP` in Terminal. On Windows, enter it manually if automatic lookup fails.

## Automation and display safety

| Setting | Default | Conditions |
| --- | --- | --- |
| Wake TV on resume | On | TV paired; bound external display connected |
| Select HDMI on resume | On | Same gate; bounded TV readiness retries |
| Power off TV on suspend | On | Recent display snapshot, display still confirmed, active TV app exactly matches the Deck HDMI input |
| Wake / select HDMI on display connection | Off | Two stable samples; uses the wake/HDMI options above |

- DRM status and EDID are checked every second under `/sys/class/drm/card*-*/`. HDMI and DisplayPort docks are supported; internal eDP/LVDS/DSI panels are excluded. Missing/invalid EDID, unknown status or a snapshot older than three seconds block automation.
- Display identity is a SHA-256 hash of a complete, valid EDID explicitly bound by the user. There is no guaranteed automatic mapping between EDID and the TV's network endpoint. Displays with identical EDIDs can share an identity. Dock/EDID changes require rebinding.
- If a dock stops reporting a connected display and EDID while the TV is off, automatic wake is blocked. Manual wake remains available.
- Resume checks the display again immediately before wake and HDMI switching. Disconnecting the dock cancels pending work and does not power off the TV.
- While the Deck is awake, paired and docked with power-off on suspend enabled, the backend prepares an authenticated TV connection and tests the active-input query in advance. This avoids spending the short suspend window on a fresh TCP/TLS/SSAP handshake and the first input query. The query result is not cached: suspend reads the current input again. Preparation sends no power, wake or HDMI command. It retries at most every five seconds, closes on undocking/configuration changes and is cleaned up on suspend/unload.
- Suspend reuses that connection when available, otherwise makes one bounded connection attempt. It uses a snapshot captured before `PrepareForSleep`, reads the active TV app and checks the display again before power-off. Another input/app, an unknown response or a timeout leaves the TV on.
- TV operations are serialized. Suspend cancels pending resume work; duplicate signals are coalesced. Loading the plugin does not issue automatic TV commands.
- The Python backend listens to systemd-logind over D-Bus, even with the panel closed. A **delay** inhibitor is released before sleep. The deadline is at most two seconds, reduced according to `InhibitDelayMaxUSec`; the power-off operation also has a 1.5-second limit.
- No privileged services or system hooks are installed, and the read-only SteamOS partition is not changed.
- If D-Bus/logind or its permissions fail, suspend/resume automation is disabled and the panel shows the failed stage and reason. Manual controls and the independent display-connection option remain available.

## Diagnose automation on the Deck

If **Suspend / resume** shows **Unavailable**, read the detail below it. The diagnostic identifies missing dependencies, a missing system bus socket, unavailable logind, permission denial, timeout or Unix descriptor passing failure. The same safe diagnostic is written to `~/homebrew/logs/lg-tv-control/plugin.log`, without pairing keys.

Install the full ZIP, including `py_modules`, then choose **Recheck suspend automation**. Share the exact new status if it remains unavailable. For a TV that stays on during sleep, check **Last suspend** after resuming; this result is preserved separately from resume activity. It identifies disabled settings, missing pairing, stale display state, another HDMI/app, connection/read failures and the phase of a timeout. The same safe result is logged. **TV sleep connection: Ready** confirms that connection setup has already finished, without asserting that a later command will succeed. If loading fails, the diagnostic distinguishes a missing bundled package (such as `dbus_next`) from a missing Python runtime module (such as `xml`) and names the module. Identify the cause before changing system permissions or running anything as root. Manual TV controls remain available.

HDMI switching requests the webOS `LAUNCH` permission. The test TV returned `401 insufficient permissions` without it and accepted `tv/switchInput` with it using the existing pairing. Permission errors now explain that authorization needs renewal.

## Settings and credentials

Settings and the pairing key are stored atomically in `DECKY_PLUGIN_SETTINGS_DIR/config.json`, normally `~/homebrew/settings/lg-tv-control/config.json`, with mode `0600`. The key is not returned to the frontend or written to logs. It is not encrypted at rest; processes running as your user can read it.

**Forget pairing** deletes the local key. Changing the host clears pairing and display binding. Uninstalling through Decky deletes the configuration file; there are no external services or hooks to remove.

TLS on port 3001 is the default. LG TVs typically use self-signed certificates, so certificate validation is disabled for this local TV connection. Use a trusted LAN. Older TVs may require plain WebSocket on port 3000. Home Assistant, HDMI-CEC and root privileges are not required.

## Run locally with a real TV

Open the interface in a macOS/Linux/Windows browser and control your real TV over the LAN without Decky. Discovery and TV responses are real. Browser components adapt the Decky interface; the Python control code is shared with the plugin. Local mode does not start suspend/resume automation or dock monitoring.

Install dependencies once:

```sh
npx --yes pnpm@9.15.9 install --frozen-lockfile
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Start both services (the project venv is detected automatically):

```sh
pnpm dev
```

Open **http://127.0.0.1:5173**, find or enter the TV address, save and pair. Buttons send real commands. Press Ctrl+C to stop both services. To run them separately, use `pnpm dev:backend` with the venv active and `pnpm dev:ui`.

Local credentials live in `.local-state/config.json`, which is ignored by Git and separate from Deck settings. The Python server only accepts local browser origins, binds to `127.0.0.1:8081` and does not expose the API to the LAN. The interface uses port 5173. **Suspend / resume** intentionally shows **Disabled** in local mode.

`pnpm typecheck:local` and `pnpm build:local` validate the browser interface. Its build goes to `out/local-preview/` and is separate from the Decky ZIP.

## Develop and build

Requirements: Node.js 20.19+, pnpm **9**, Python **3.11+** with pip/venv. Decky's Python runtime must also support Python 3.11+ (`asyncio.timeout`). The ZIP contains portable Python dependencies, not macOS binaries. The entry point explicitly loads `py_modules` relative to its own installed location, including when Decky renames the plugin folder.

```sh
corepack pnpm install --frozen-lockfile
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
pnpm typecheck
pnpm typecheck:local
python -m unittest discover -s tests -v
pnpm build
python scripts/package.py
```

Without Corepack, replace `pnpm` with `npx --yes pnpm@9.15.9`. The packager downloads pinned Python wheels from `requirements.txt`, preserves dependency licenses/metadata in `py_modules/` and creates `out/lg-tv-control-v0.1.9.zip`. Network access is needed during dependency installation/build. The ZIP has a single `lg-tv-control/` folder containing metadata, `main.py`, `dist/index.js`, backend code, dependencies and the license.

CI runs type checks, backend/protocol tests, browser and Decky builds, and packaging. Tests exercise display gates, credentials, SSAP commands, Wake-on-LAN, logind signals and inhibitor cleanup. When dbus-daemon is installed, an isolated real bus also tests Unix file descriptor passing. The pinned dbus-next 0.2.3 bundle has a small compatibility patch: XML imports and their type annotations are deferred until XML introspection is requested. The raw D-Bus calls used by this plugin do not require XML, which some frozen Decky runtimes omit. The patch is applied by `scripts/bundle_dependencies.py`, preserves dependency licenses, and is checked against the pinned source. Packaging validates dependency imports with host site-packages and XML disabled. Integration tests repeat real D-Bus calls, FD passing and sleep signals in that isolated environment; a bootstrap test also checks renamed install folders. Tests do not replace physical hardware validation. Store publication requires the Decky submission/review process.

## Hardware checklist

- [ ] Pair, restart Decky/Deck and confirm persistence without a new prompt.
- [ ] Verify manual wake, power-off and every supported HDMI input.
- [ ] Resume/suspend in handheld mode without any TV commands.
- [ ] Resume docked with the TV off; wake/select only when DRM/EDID remains verified.
- [ ] Suspend with the Deck HDMI active; confirm bounded power-off.
- [ ] Suspend with another input/app or an unavailable TV; confirm it stays on and the deadline is respected.
- [ ] Test unplugging, another display, missing EDID, rapid reconnection and network changes.
- [ ] Test Gaming Mode and Desktop Mode with the panel closed.
- [ ] Uninstall through Decky; confirm credential removal and no remaining plugin inhibitor in `systemd-inhibit --list`.

## Base and references

Based on the [official Decky template](https://github.com/SteamDeckHomebrew/decky-plugin-template), revision `90d0780e882a17f5714fc6de044c645f22608290`. Its BSD license is preserved.

- [Decky documentation](https://wiki.deckbrew.xyz/en/plugin-dev/getting-started): structure, metadata and backend. Some wiki examples use the legacy API; this frontend follows the current template with `@decky/api`, `@decky/ui` and API v1.
- [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader): lifecycle, private directories and `py_modules` loading.
- [systemd-logind](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.login1.html): sleep signals, inhibitors and deadlines.
- [aiowebostv](https://github.com/home-assistant-libs/aiowebostv): SSAP registration and endpoint reference; not bundled. Its current version requires Python 3.12+ and aiohttp; this plugin uses a small adapter over websockets.
- [lgctl](https://github.com/lmrisdal/lgctl): evaluated as an alternative; not bundled. The consulted checkout had no license file and used a Go CLI with its own systemd services. The Python approach avoids an architecture-specific executable and privileged service installation.
