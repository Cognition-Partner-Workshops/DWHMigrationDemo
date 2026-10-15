from __future__ import annotations

import atexit
import ctypes
import logging
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

from migraai.api import auth, health, platforms, session, settings
from migraai.core.audit import AuditLog
from migraai.core.config import get_data_root
from migraai.core.logging import AccessLogger
from migraai.core.secrets import get_protector
from migraai.core.security import AuthState, security_middleware
from migraai.core.settings_store import SettingsStore
from migraai.core.storage import wipe_sessions

logger = logging.getLogger(__name__)
_console_handlers: list[Any] = []


def _register_console_cleanup(data_dir: Path) -> None:
    if os.name != "nt":
        return

    handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    @handler_type
    def handler(event: int) -> bool:
        if event == 2:
            wipe_sessions(data_dir)
            return True
        return False

    if not ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True):
        logger.warning("Could not register Windows console shutdown handler.")
    else:
        _console_handlers.append(handler)


def _serve_spa(web_dir: Path | None) -> Callable[[str], Response]:
    def fallback(path: str) -> Response:
        if web_dir is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Frontend is not configured.")
        root = web_dir.resolve()
        index = root / "index.html"
        if not index.is_file():
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Frontend is not configured.")
        requested = (root / path).resolve() if path else index
        if requested != root and root not in requested.parents:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found.")
        if requested.is_file():
            return FileResponse(requested)
        return FileResponse(index)

    return fallback


def create_app(
    web_dir: Path | None = None,
    data_dir: Path | None = None,
) -> FastAPI:
    resolved_data_dir = Path(data_dir or get_data_root()).expanduser().resolve()
    resolved_web_dir = Path(web_dir).expanduser().resolve() if web_dir else None
    auth_state = AuthState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_data_dir.mkdir(parents=True, exist_ok=True)
        wipe_sessions(resolved_data_dir)
        app.state.settings_store.initialize()
        app.state.audit_log = AuditLog(resolved_data_dir / "logs" / "audit.jsonl")
        app.state.access_logger = AccessLogger(resolved_data_dir / "logs" / "access.log")
        app.state.audit_log.append("system", "app.start", "application")
        try:
            yield
        finally:
            app.state.audit_log.append("system", "app.stop", "application")
            wipe_sessions(resolved_data_dir)

    app = FastAPI(title="MigraAI", version="0.1.0", lifespan=lifespan)
    app.state.data_dir = resolved_data_dir
    app.state.auth = auth_state
    app.state.current_session = None
    app.state.settings_store = SettingsStore(resolved_data_dir, get_protector(resolved_data_dir))
    atexit.register(wipe_sessions, resolved_data_dir)
    _register_console_cleanup(resolved_data_dir)

    @app.middleware("http")
    async def security_layer(request: Request, call_next: Callable[..., Any]) -> Response:
        return await security_middleware(request, call_next, auth_state)

    @app.middleware("http")
    async def access_log(request: Request, call_next: Callable[..., Any]) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            response = Response(status_code=500)
            _write_access_log(request, response, started)
            raise
        _write_access_log(request, response, started)
        return response

    def _write_access_log(request: Request, response: Response, started: float) -> None:
        access_logger: AccessLogger | None = getattr(app.state, "access_logger", None)
        if access_logger is None:
            return
        access_logger.write(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=(time.perf_counter() - started) * 1000,
            session_id=request.cookies.get("migraai_session"),
        )

    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(platforms.router)
    app.include_router(settings.router)
    app.include_router(session.router)

    spa_fallback = _serve_spa(resolved_web_dir)

    @app.get("/{path:path}", include_in_schema=False)
    def serve_frontend(path: str = "") -> Response:
        if path.startswith("api/") or path == "api":
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found.")
        return spa_fallback(path)

    return app
