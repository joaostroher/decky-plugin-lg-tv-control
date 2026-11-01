"""Loopback-only development RPC for real TVs. No suspend/resume automation."""
import asyncio
import json
import sys
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.controller import Controller
from backend.settings import Settings

METHODS = {"get_state", "save_settings", "bind_display", "discover_tvs", "detect_tv_mac", "action", "retry_automation"}
ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


def handler_for(controller, loop):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if (self.headers.get("Origin") not in ORIGINS
                    or self.headers.get("Host") not in {"127.0.0.1:8081", "localhost:8081"}):
                self.reply(403, {"error": "Origin not allowed."})
                return
            method = self.path.removeprefix("/rpc/")
            if not self.path.startswith("/rpc/") or method not in METHODS:
                self.reply(404, {"error": "Unknown method."})
                return
            future = None
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536 or self.headers.get_content_type() != "application/json":
                    raise ValueError()
                arguments = json.loads(self.rfile.read(size))
                if not isinstance(arguments, list):
                    raise ValueError()
                future = asyncio.run_coroutine_threadsafe(getattr(controller, method)(*arguments), loop)
                result = future.result(timeout=40)
                self.reply(200, {"result": result})
            except (ValueError, TypeError):
                self.reply(400, {"error": "Invalid request."})
            except FutureTimeout:
                if future:
                    future.cancel()
                self.reply(504, {"error": "The operation timed out."})
            except Exception:
                self.reply(500, {"error": "Operation failed. Check the TV and network."})
    return Handler


async def main():
    controller = Controller()
    controller._configure(Settings(ROOT / ".local-state"))
    controller.lifecycle.status = "Local mode: suspend/resume automation disabled."
    controller.lifecycle.state = "disabled"
    server = ThreadingHTTPServer(("127.0.0.1", 8081), handler_for(controller, asyncio.get_running_loop()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print("Local backend running at http://127.0.0.1:8081 — commands target the real TV.", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
