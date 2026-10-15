from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from migraai.core.audit import AuditLog
from migraai.core.crypto import EncryptedFileStore, SessionCipher
from migraai.core.storage import create_session_dir
from migraai.registry.platforms import SOURCES, TARGETS

router = APIRouter(prefix="/api/session", tags=["session"])
SOURCE_IDS = {platform["id"] for platform in SOURCES}
TARGET_IDS = {platform["id"] for platform in TARGETS}


class SessionConfirm(BaseModel):
    source: str
    target: str


class SessionConfirmed(BaseModel):
    session_id: str
    source: str
    target: str


@router.post("/confirm", response_model=SessionConfirmed)
def confirm_session(payload: SessionConfirm, request: Request) -> SessionConfirmed:
    if payload.source not in SOURCE_IDS:
        raise HTTPException(status_code=422, detail="Unknown source platform.")
    if payload.target not in TARGET_IDS:
        raise HTTPException(status_code=422, detail="Unknown target platform.")
    if payload.source == payload.target:
        raise HTTPException(status_code=422, detail="Source and target platforms must differ.")

    session_dir: Path = create_session_dir(request.app.state.data_dir)
    session_id = session_dir.name
    cipher = SessionCipher()
    request.app.state.current_session = {
        "session_id": session_id,
        "source": payload.source,
        "target": payload.target,
        "store": EncryptedFileStore(session_dir, cipher),
        "cipher": cipher,
    }
    audit: AuditLog = request.app.state.audit_log
    audit.append(
        "user",
        "session.confirm",
        session_id,
        {"source": payload.source, "target": payload.target},
    )
    return SessionConfirmed(session_id=session_id, source=payload.source, target=payload.target)
