"""Build a Decky ZIP from explicit inputs, including pure Python dependencies."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from bundle_dependencies import prepare_dbus_next

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = json.loads((ROOT / "package.json").read_text())["version"]
    output = ROOT / "out" / f"lg-tv-control-v{version}.zip"
    output.parent.mkdir(exist_ok=True)
    if not (ROOT / "dist/index.js").is_file():
        raise SystemExit("Run pnpm build first")
    with tempfile.TemporaryDirectory() as directory:
        modules = Path(directory) / "py_modules"
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--only-binary=:all:", "--platform=any", "--python-version=3.11",
            "--implementation=py", "--abi=none", "--no-compile", "--target", str(modules),
            "-r", str(ROOT / "requirements.txt")], check=True)
        prepare_dbus_next(modules)
        # Never let this machine's site-packages conceal a missing ZIP dependency.
        # Also reproduce a frozen runtime which does not include stdlib XML.
        subprocess.run([sys.executable, "-I", "-S", "-c", '''
import importlib.abc
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
class WithoutXML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "xml" or fullname.startswith("xml."):
            raise ModuleNotFoundError("XML is intentionally unavailable", name=fullname)
sys.meta_path.insert(0, WithoutXML())
import dbus_next
from dbus_next.aio import MessageBus
from websockets.legacy.client import connect
assert pathlib.Path(dbus_next.__file__).is_relative_to(pathlib.Path(sys.argv[1]))
print("Bundled dependencies import successfully without host site-packages or XML.")
''', str(modules)], check=True)
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for name in ["package.json", "plugin.json", "main.py", "README.md", "LICENSE", "requirements.txt"]:
                archive.write(ROOT / name, "lg-tv-control/" + name)
            for directory_name in ["dist", "backend"]:
                for path in sorted((ROOT / directory_name).rglob("*")):
                    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                        archive.write(path, "lg-tv-control/" + str(path.relative_to(ROOT)))
            for path in sorted(modules.rglob("*")):
                if path.is_file():
                    archive.write(path, "lg-tv-control/py_modules/" + str(path.relative_to(modules)))
    print(output)


if __name__ == "__main__":
    main()
