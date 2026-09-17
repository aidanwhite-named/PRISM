"""PRISM desktop/stdio entry point. Console stays available for Ctrl+C."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import time
import urllib.request
import webbrowser

from app.runtime import is_frozen, prepare_frozen_runtime, seed_prompts


class InstanceLock:
    def __init__(self, directory: Path):
        self.path = directory / "desktop.json"
        self.stream = None
        directory.mkdir(parents=True, exist_ok=True)
        stream = (directory / "desktop.lock").open("a+b")
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
        else:
            self.stream = stream

    def close(self):
        if self.stream:
            self.path.unlink(missing_ok=True)
            self.stream.close()
            self.stream = None


def contact_instance(path: Path, *, stop: bool = False) -> str:
    state = json.loads(path.read_text(encoding="utf-8"))
    port = int(state["port"])
    if not 1 <= port <= 65535:
        raise ValueError("Invalid instance port")
    url = f"http://127.0.0.1:{port}"
    request = urllib.request.Request(
        url + "/api/desktop/instance",
        data=b"" if stop else None,
        headers={"X-PRISM-Desktop": state["token"], "X-PRISM-Client": "1"},
        method="POST" if stop else "GET",
    )
    # Do not send instance credentials through a configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=2) as response:
        if json.load(response).get("application") != "PRISM":
            raise ValueError("Not a PRISM desktop instance")
    return url


def bind_port(start: int) -> socket.socket:
    for port in range(start, min(start + 21, 65536)):
        sock = socket.socket()
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", port))
            sock.listen(128)
            sock.setblocking(False)
            return sock
        except OSError:
            sock.close()
    raise OSError("No free local port. Use --port to choose another port.")


async def serve(sock: socket.socket, state_path: Path, no_browser: bool):
    import uvicorn
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from app.main import app

    token = secrets.token_urlsafe(32)
    port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, loop="asyncio", http="h11",
        ws="none", access_log=False, timeout_graceful_shutdown=5,
    ))

    # Register before the catch-all SPA route. Request is supplied explicitly
    # through annotations because this module uses postponed annotations.
    async def instance(request):
        if not secrets.compare_digest(request.headers.get("x-prism-desktop", ""), token):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        if request.method == "POST":
            server.should_exit = True
        return {"application": "PRISM"}

    instance.__annotations__["request"] = Request
    app.add_api_route("/api/desktop/instance", instance, methods=["GET", "POST"], include_in_schema=False)
    app.router.routes.insert(0, app.router.routes.pop())

    async def announce():
        while not server.started and not server.should_exit:
            await asyncio.sleep(0.05)
        if server.started:
            state_path.write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
            print(f"\nPRISM: {url}\nStop: Ctrl+C or PRISM.exe --stop\n", flush=True)
            if not no_browser:
                await asyncio.to_thread(webbrowser.open, url)

    ready = asyncio.create_task(announce())
    try:
        await server.serve(sockets=[sock])
    finally:
        ready.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ready


def main() -> int:
    prepare_frozen_runtime()
    if sys.argv[1:] == ["--search-mcp"]:
        from app.search_mcp_server import main as search_main
        search_main()
        return 0
    parser = argparse.ArgumentParser(description="PRISM local patent workspace")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PRISM_PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stop", action="store_true", help="Stop this data directory's running PRISM")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    from app.config import default_data_dir, default_prompt_dir

    lock = InstanceLock(default_data_dir())
    try:
        if not lock.stream:
            for attempt in range(30):
                try:
                    url = contact_instance(lock.path, stop=args.stop)
                    if not args.stop and not args.no_browser:
                        webbrowser.open(url)
                    print("PRISM stopping." if args.stop else f"PRISM already running: {url}")
                    return 0
                except (OSError, ValueError, KeyError):
                    if attempt == 29:
                        raise RuntimeError("PRISM is running but not responding. Check its console.")
                    time.sleep(0.2)
        if args.stop:
            print("PRISM is not running.")
            return 0
        if is_frozen():
            seed_prompts(default_prompt_dir())
        with bind_port(args.port) as sock:
            # config was imported to find paths, so update its exported port too.
            from app import config
            config.HOST = "127.0.0.1"
            config.PORT = sock.getsockname()[1]
            os.environ["PRISM_HOST"] = config.HOST
            os.environ["PRISM_PORT"] = str(config.PORT)
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            asyncio.run(serve(sock, lock.path, args.no_browser))
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback
        traceback.print_exc()
        if is_frozen() and sys.stdin.isatty():
            with contextlib.suppress(EOFError):
                input("PRISM failed to start. Press Enter to close.")
        raise SystemExit(1)
