from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from migraai.main import create_app


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


def _open_browser_when_ready(server: uvicorn.Server, url: str) -> None:
    while not server.started and not server.should_exit:
        time.sleep(0.05)
    if server.started:
        webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m migraai")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--web-dir", type=Path)
    args = parser.parse_args()

    if args.host.casefold() not in {"127.0.0.1", "localhost"}:
        parser.error("--host must be 127.0.0.1 or localhost")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    host = "127.0.0.1"
    port = args.port or _free_port(host)
    app = create_app(web_dir=args.web_dir)
    launch_url = f"http://{host}:{port}/?launch={app.state.auth.launch_token}"
    print(f"MigraAI running at {launch_url}", flush=True)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, access_log=False))
    if not args.no_browser:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(server, launch_url),
            daemon=True,
        ).start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
