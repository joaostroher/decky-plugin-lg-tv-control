"""Fail-closed DRM discovery. EDID identity must be explicitly bound by the user."""
import hashlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Display:
    connector: str
    state: str
    identity: str = ""


@dataclass
class Snapshot:
    at: float
    displays: list

    def trusted(self, identity, now=None):
        age = (time.monotonic() if now is None else now) - self.at
        return bool(identity) and 0 <= age <= 3 and any(
            d.state == "connected" and d.identity == identity for d in self.displays)

    def public(self):
        return [asdict(d) for d in self.displays]


def scan(root="/sys/class/drm"):
    displays = []
    for status in sorted(Path(root).glob("card*-*/status")):
        name = status.parent.name
        # Internal panels never authorize automation. DP docks are supported.
        if any(part in name for part in ("-eDP-", "-LVDS-", "-DSI-")):
            continue
        state, identity = "unknown", ""
        try:
            first = status.read_text().strip()
            edid = (status.parent / "edid").read_bytes() if first == "connected" else b""
            second = status.read_text().strip()
            if first == second and first in ("connected", "disconnected"):
                state = first
            # Validate complete EDID blocks and checksums before trusting identity.
            if (state == "connected" and len(edid) >= 128 and len(edid) % 128 == 0
                    and edid[:8] == b"\x00\xff\xff\xff\xff\xff\xff\x00"
                    and len(edid) == (1 + edid[126]) * 128
                    and all(sum(edid[i:i+128]) % 256 == 0 for i in range(0, len(edid), 128))):
                identity = hashlib.sha256(edid).hexdigest()
        except OSError:
            pass
        displays.append(Display(name, state, identity))
    return Snapshot(time.monotonic(), displays)
