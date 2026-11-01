"""Prepare portable dependencies for Decky's reduced, frozen Python runtime."""
from pathlib import Path


LAZY_XML = '''"""Load optional XML introspection support only when it is actually used.

LG TV Control compatibility patch for dbus-next 0.2.3. Raw D-Bus calls and
Unix FD passing do not require XML; some frozen Decky runtimes omit it.
The original dbus-next license remains in its distribution metadata.
"""
import importlib


class _LazyElementTree:
    def __getattr__(self, name):
        return getattr(importlib.import_module("xml.etree.ElementTree"), name)


ET = _LazyElementTree()
'''


def prepare_dbus_next(modules):
    root = Path(modules) / "dbus_next"
    for relative in ("introspection.py", "message_bus.py", "proxy_object.py", "aio/proxy_object.py", "glib/proxy_object.py"):
        source = root / relative
        text = source.read_text()
        original = "import xml.etree.ElementTree as ET"
        if text.count(original) != 1 or "from __future__ import annotations" in text:
            raise RuntimeError("Unexpected dbus-next source; review the pinned compatibility patch: " + relative)
        # Defer ElementTree annotations too: evaluating ET.Element would otherwise
        # load XML while importing the dependency, before any introspection call.
        source.write_text("from __future__ import annotations\n" + text.replace(original, "from dbus_next._lazy_xml import ET"))
    (root / "_lazy_xml.py").write_text(LAZY_XML)
