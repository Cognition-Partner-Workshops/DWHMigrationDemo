from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from migraai.core.audit import AuditLog
from migraai.kb.refresh import RefreshJob
from migraai.kb.search import hybrid_search
from migraai.kb.store import KBStore, connect

router = APIRouter(prefix="/api/kb", tags=["knowledge base"])


def _store(request: Request) -> KBStore:
    return request.app.state.kb_store


def _running(request: Request) -> bool:
    job = getattr(request.app.state, "kb_refresh_job", None)
    return bool(job and not job.done)


@router.get("/status")
def status(request: Request) -> dict:
    store = _store(request)
    current = store.current_version
    db = store.current_path
    documents = chunks = 0
    created_at = None
    if db:
        with connect(db) as connection:
            row = connection.execute(
                "SELECT created_at,documents,chunks FROM kb_versions WHERE version=?", (current,)
            ).fetchone()
            if row:
                created_at, documents, chunks = row["created_at"], row["documents"], row["chunks"]
    job = getattr(request.app.state, "kb_refresh_job", None)
    return {
        "current_version": current,
        "created_at": created_at,
        "documents": documents,
        "chunks": chunks,
        "last_refresh_summary": store.state().get("last_refresh_summary"),
        "running": job.snapshot() if job and not job.done else None,
        "rollback_versions": [version for version in store.versions() if version != current],
    }


@router.post("/refresh", status_code=202)
def start_refresh(request: Request) -> dict:
    with request.app.state.kb_job_lock:
        if _running(request):
            raise HTTPException(status_code=409, detail="A KB refresh is already running.")
        job = RefreshJob(
            _store(request),
            Path(__file__).resolve().parents[1] / "kb" / "seed",
            audit=lambda action, details: request.app.state.audit_log.append(
                "user", action, "knowledge-base", details
            ),
        )
        request.app.state.kb_refresh_job = job
        job.start()
    return {"status": "started"}


@router.delete("/refresh", status_code=202)
def cancel_refresh(request: Request) -> dict:
    job = getattr(request.app.state, "kb_refresh_job", None)
    if not job or job.done:
        raise HTTPException(status_code=409, detail="No KB refresh is running.")
    job.cancel()
    return {"status": "cancelling"}


@router.post("/rollback")
def rollback(request: Request) -> dict:
    with request.app.state.kb_job_lock:
        if _running(request):
            raise HTTPException(status_code=409, detail="Cannot roll back during a KB refresh.")
        version = _store(request).rollback()
    if version is None:
        raise HTTPException(status_code=409, detail="No previous KB version is available.")
    audit: AuditLog = request.app.state.audit_log
    audit.append("user", "kb.rollback", "knowledge-base", {"version": version})
    return {"current_version": version}


@router.get("/search")
def search(
    request: Request,
    q: str = Query(min_length=1),
    source: str | None = None,
    target: str | None = None,
    section: str | None = None,
    k: int = Query(default=8, ge=1, le=50),
) -> dict:
    path = _store(request).current_path
    if path is None:
        raise HTTPException(status_code=503, detail="Knowledge base is not initialized.")
    hits = hybrid_search(q, source=source, target=target, section=section, k=k, db_path=path)
    return {"results": [hit.__dict__ for hit in hits]}
