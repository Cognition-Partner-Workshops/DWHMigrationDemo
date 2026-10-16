from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from migraai.core.audit import AuditLog
from migraai.core.crypto import EncryptedFileStore, SessionCipher
from migraai.core.storage import create_session_dir, wipe_session_dir
from migraai.discovery.inventory import Inventory
from migraai.discovery.runner import DiscoveryRunner
from migraai.registry.platforms import SOURCES, TARGETS

router = APIRouter(prefix="/api/session", tags=["session"])
SOURCE_IDS = {platform["id"] for platform in SOURCES}
TARGET_IDS = {platform["id"] for platform in TARGETS}


class SessionConfirm(BaseModel):
    source: str
    target: str


class CurrentSession(BaseModel):
    source: str | None
    target: str | None
    confirmed: bool


class SessionConfirmed(BaseModel):
    session_id: str
    source: str
    target: str
    plan: dict


@router.get("", response_model=CurrentSession)
def get_current_session(request: Request) -> CurrentSession:
    session = request.app.state.current_session
    if not session:
        return CurrentSession(source=None, target=None, confirmed=False)
    return CurrentSession(source=session["source"], target=session["target"], confirmed=True)


@router.post("/confirm", response_model=SessionConfirmed)
def confirm_session(payload: SessionConfirm, request: Request) -> SessionConfirmed:
    if payload.source not in SOURCE_IDS:
        raise HTTPException(status_code=422, detail="Unknown source platform.")
    if payload.target not in TARGET_IDS:
        raise HTTPException(status_code=422, detail="Unknown target platform.")
    if payload.source == payload.target:
        raise HTTPException(status_code=422, detail="Source and target platforms must differ.")

    previous_session = request.app.state.current_session
    previous_session_id = previous_session["session_id"] if previous_session else None
    if previous_session_id:
        discovery = previous_session.get("discovery")
        if discovery is not None:
            discovery.close()
        inventory = previous_session.get("inventory")
        if inventory is not None:
            inventory.close()
        previous_session_dir = request.app.state.data_dir / "sessions" / previous_session_id
        wipe_session_dir(previous_session_dir)

    session_dir: Path = create_session_dir(request.app.state.data_dir)
    session_id = session_dir.name
    cipher = SessionCipher()
    kb_store = getattr(request.app.state, "kb_store", None)
    current_session = {
        "session_id": session_id,
        "source": payload.source,
        "target": payload.target,
        "kb_version": getattr(kb_store, "current_version", None),
        "store": EncryptedFileStore(session_dir, cipher),
        "cipher": cipher,
        "inventory": Inventory(),
        "uploads": {},
        "upload_lock": threading.RLock(),
        "upload_bytes_received": 0,
        "zip_uncompressed_bytes": 0,
    }
    current_session["discovery"] = DiscoveryRunner(current_session, request.app.state.audit_log)
    request.app.state.current_session = current_session
    audit: AuditLog = request.app.state.audit_log
    if previous_session_id:
        audit.append(
            "user",
            "session.replace",
            previous_session_id,
            {"new_session_id": session_id},
        )
    audit.append(
        "user",
        "session.confirm",
        session_id,
        {"source": payload.source, "target": payload.target},
    )
    from migraai.api.plan import start_plan_flow

    plan = start_plan_flow(request, regenerate=False)
    return SessionConfirmed(
        session_id=session_id, source=payload.source, target=payload.target, plan=plan
    )
