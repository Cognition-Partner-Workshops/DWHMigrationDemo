from __future__ import annotations

import atexit
import ctypes
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

from migraai.api import (
    auth,
    health,
    inventory,
    kb,
    plan,
    platforms,
    resources,
    session,
    settings,
    uploads,
)
from migraai.core.audit import AuditLog
from migraai.core.config import get_data_root
from migraai.core.logging import AccessLogger
from migraai.core.secrets import get_protector
from migraai.core.security import AuthState, security_middleware
from migraai.core.settings_store import SettingsStore
from migraai.core.storage import wipe_sessions
from migraai.kb.build import build_database
from migraai.kb.store import (
    KBStore,
    compute_seed_hash,
    connect,
    create_schema,
    seed_database_hash,
    upgrade_seed_database,
)
from migraai.kb.validate import validate_seed_dir
from migraai.planning.store import PlanStore

logger = logging.getLogger(__name__)
_console_handlers: list[Any] = []


def _register_console_cleanup(data_dir: Path) -> None:
    if os.name != "nt":
        return

    handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    @handler_type
    def handler(event: int) -> bool:
        if event in {2, 5, 6}:
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


def _initialize_kb(store: KBStore, seed_dir: Path, bundled: Path, audit: AuditLog) -> int | None:
    store.initialize()
    current = store.current_path
    seed_files = list(seed_dir.rglob("*.yaml")) if seed_dir.is_dir() else []
    source = bundled if bundled.is_file() else None
    temporary_seed: Path | None = None
    seed_error: int | None = None

    def report_seed_error(error_count: int) -> None:
        nonlocal seed_error
        seed_error = max(1, error_count)
        logger.warning("Knowledge-base seed validation failed (%d errors).", seed_error)
        audit.append(
            "system",
            "kb.seed_error",
            "knowledge-base",
            {"error_count": seed_error},
        )

    if source is None and seed_files:
        try:
            errors, _ = validate_seed_dir(seed_dir, allow_incomplete=True)
            if errors:
                report_seed_error(len(errors))
            else:
                temporary_seed = store.root / ".seed-build.partial.db"
                build_database(seed_dir, temporary_seed, allow_incomplete=True)
                source = temporary_seed
        except Exception:
            report_seed_error(1)

    if source is not None:
        try:
            seed_database_hash(source)
        except Exception:
            report_seed_error(1)
            source = None

    try:
        if current is None:
            if source is None:
                store.create_empty()
                return seed_error
            destination = store.version_path(1)
            destination.unlink(missing_ok=True)
            with (
                closing(connect(source)) as source_connection,
                closing(connect(destination)) as destination_connection,
            ):
                source_connection.backup(destination_connection)
                create_schema(destination_connection)
                seed_hash = compute_seed_hash(destination_connection)
                destination_connection.execute("UPDATE kb_versions SET version=1")
                destination_connection.execute("UPDATE kb_versions SET seed_sha256=?", (seed_hash,))
                if destination_connection.execute("SELECT changes()").fetchone()[0] == 0:
                    destination_connection.execute(
                        """INSERT INTO kb_versions
                        (version,created_at,documents,chunks,seed_sha256) VALUES(1,?,?,?,?)""",
                        (
                            datetime.now(UTC).isoformat(),
                            destination_connection.execute(
                                "SELECT count(*) FROM documents"
                            ).fetchone()[0],
                            destination_connection.execute(
                                "SELECT count(*) FROM chunks"
                            ).fetchone()[0],
                            seed_hash,
                        ),
                    )
                destination_connection.commit()
            store.activate(1)
            return seed_error

        current_hash = seed_database_hash(current)
        if source is None:
            return seed_error
        incoming_hash = seed_database_hash(source)
        if incoming_hash == current_hash:
            return seed_error
        version = int(store.current_version or 0) + 1
        upgrade_seed_database(source, current, store.version_path(version), version)
        store.activate(version)
        audit.append(
            "system",
            "kb.seed_upgrade",
            "knowledge-base",
            {"from_version": version - 1, "to_version": version},
        )
        return seed_error
    finally:
        if temporary_seed:
            temporary_seed.unlink(missing_ok=True)


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
        app.state.kb_store = KBStore(resolved_data_dir)
        configured_seed_kb = os.environ.get("MIGRAAI_SEED_KB")
        bundled = (
            Path(sys.executable).resolve().parent.parent / "app" / "kb" / "kb-v1.db"
            if configured_seed_kb is None
            else Path(configured_seed_kb)
        )
        seed_dir = Path(os.environ.get("MIGRAAI_SEED_DIR", Path(__file__).with_name("kb") / "seed"))
        app.state.kb_seed_dir = seed_dir
        app.state.kb_seed_error = _initialize_kb(
            app.state.kb_store,
            seed_dir,
            bundled,
            app.state.audit_log,
        )
        app.state.plan_store = PlanStore(resolved_data_dir / "app.db")
        app.state.plan_store.initialize()
        app.state.plan_job = None
        app.state.kb_refresh_job = None
        app.state.kb_job_lock = threading.Lock()
        try:
            yield
        finally:
            current_session = app.state.current_session
            if current_session:
                discovery = current_session.get("discovery")
                if discovery is not None:
                    discovery.close()
                inventory_store = current_session.get("inventory")
                if inventory_store is not None:
                    inventory_store.close()
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
    app.include_router(kb.router)
    app.include_router(plan.router)
    app.include_router(resources.router)
    app.include_router(uploads.router)
    app.include_router(inventory.router)

    spa_fallback = _serve_spa(resolved_web_dir)

    @app.get("/{path:path}", include_in_schema=False)
    def serve_frontend(path: str = "") -> Response:
        if path.startswith("api/") or path == "api":
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found.")
        return spa_fallback(path)

    return app
