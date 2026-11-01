import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.bundle_dependencies import prepare_dbus_next


class BundledDependencyTests(unittest.TestCase):
    def test_raw_client_loads_without_xml_and_keeps_optional_xml_working(self):
        import dbus_next
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(Path(dbus_next.__file__).parent, root / "dbus_next",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            prepare_dbus_next(root)
            code = '''
import importlib.abc, pathlib, sys
sys.path.insert(0, sys.argv[1])
class WithoutXML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "xml" or fullname.startswith("xml."):
            raise ModuleNotFoundError("No XML in frozen runtime", name=fullname)
blocker = WithoutXML()
sys.meta_path.insert(0, blocker)
import dbus_next
from dbus_next.aio import MessageBus
from dbus_next import Message, BusType
assert pathlib.Path(dbus_next.__file__).is_relative_to(pathlib.Path(sys.argv[1]))
assert not any(name == "xml" or name.startswith("xml.") for name in sys.modules)
dbus_next.introspection.Node.default()
sys.meta_path.remove(blocker)
node = dbus_next.introspection.Node.parse('<node name="/test"/>')
assert node.name == "/test"
assert "<node" in node.tostring()
'''
            subprocess.run([sys.executable, "-I", "-S", "-c", code, str(root)], check=True)

    def test_patch_rejects_unreviewed_dependency_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dbus_next").mkdir()
            (root / "dbus_next/introspection.py").write_text("# unexpected version\n")
            with self.assertRaisesRegex(RuntimeError, "Unexpected dbus-next source"):
                prepare_dbus_next(root)
