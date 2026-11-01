import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const virtualPython = join(root, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const python = existsSync(virtualPython) ? virtualPython : "python3";
const children = [
  spawn(python, ["-u", "scripts/local_server.py"], { cwd: root, stdio: "inherit" }),
  spawn(process.execPath, [join(root, "node_modules/vite/bin/vite.js"), "--config", "dev/vite.config.js"], {
    cwd: root, stdio: "inherit",
  }),
];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of children) child.kill();
}
for (const child of children) {
  child.on("error", error => { console.error(error.message); stop(1); });
  child.on("exit", code => { if (!stopping) stop(code ?? 1); });
}
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
