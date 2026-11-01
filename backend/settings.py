"""Validated preferences and private, atomic credential storage."""
import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path


DEFAULTS = dict(host="", mac="", hdmi="HDMI_1", secure=True,
                broadcast="255.255.255.255", wake_on_resume=True,
                input_on_resume=True, off_on_suspend=True,
                wake_on_connect=False, timeout=25, retries=3, display_id="")


def validate(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError("Invalid settings.")
    result = {**DEFAULTS, **values}
    for key, default in DEFAULTS.items():
        if type(result[key]) is not type(default):
            raise ValueError("Invalid setting type: " + key)
    host = result["host"].strip()
    if host and not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", host):
        raise ValueError("Use an IPv4 address or hostname without a port or URL.")
    result["host"] = host
    mac = re.sub(r"[:-]", "", result["mac"])
    if mac and not re.fullmatch(r"[0-9a-fA-F]{12}", mac):
        raise ValueError("Invalid MAC address.")
    result["mac"] = ":".join(mac[i:i+2] for i in range(0, len(mac), 2)).upper()
    if result["hdmi"] not in {"HDMI_1", "HDMI_2", "HDMI_3", "HDMI_4"}:
        raise ValueError("Invalid HDMI input.")
    ipaddress.IPv4Address(result["broadcast"])
    if not 5 <= result["timeout"] <= 90 or not 1 <= result["retries"] <= 10:
        raise ValueError("Timeout: 5–90 seconds; retries: 1–10.")
    if result["display_id"] and not re.fullmatch(r"[0-9a-f]{64}", result["display_id"]):
        raise ValueError("Invalid display identity.")
    return result


class Settings:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "config.json"
        self.values = DEFAULTS.copy()
        self.key = None
        if self.path.exists():
            os.chmod(self.path, 0o600)
            data = json.loads(self.path.read_text())
            self.values = validate(data["settings"])
            self.key = data.get("client_key")
            if self.key is not None and not isinstance(self.key, str):
                raise ValueError("Invalid pairing key.")

    def save(self, values=None, key=None, replace_key=False):
        updated = validate(values if values is not None else self.values)
        credential = key if replace_key else self.key
        if updated["host"] != self.values["host"]:
            credential = None
            updated["display_id"] = ""
        fd, name = tempfile.mkstemp(dir=self.directory, prefix=".config-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"settings": updated, "client_key": credential}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.values, self.key = updated, credential

    def public(self):
        return {**self.values, "paired": bool(self.key)}
