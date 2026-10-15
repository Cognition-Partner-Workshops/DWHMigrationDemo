from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from migraai.discovery.uploads import UploadRow, ingest_file

router = APIRouter(prefix="/api", tags=["discovery"])
FILES_FIELD = File(...)


def _session(request: Request) -> dict[str, Any]:
    session = request.app.state.current_session
    if not session:
        raise HTTPException(status_code=409, detail="Confirm a source and target first.")
    return session


def _row(request: Request, upload_id: str) -> UploadRow:
    row = _session(request).get("uploads", {}).get(upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown upload.")
    return row


def _upload_dict(request: Request, row: UploadRow) -> dict[str, object]:
    inventory = _session(request)["inventory"]
    return {**row.to_dict(), "issue_count": inventory.issue_count(row.id)}


def _remove_upload(
    request: Request,
    session: dict[str, Any],
    upload_id: str,
) -> UploadRow | None:
    row = session.get("uploads", {}).get(upload_id)
    if row is None:
        return None
    session["inventory"].delete_upload(upload_id)
    session["uploads"].pop(upload_id, None)
    if row.store_path:
        session["store"].delete(row.store_path)
    request.app.state.audit_log.append(
        "user",
        "uploads.deleted",
        session["session_id"],
        {"count": 1, "size": row.size, "sha256": row.digest},
    )
    return row


class UploadSelection(BaseModel):
    selected: bool


@router.post("/uploads")
def post_uploads(request: Request, files: list[UploadFile] = FILES_FIELD) -> dict[str, Any]:
    session = _session(request)
    rows: list[UploadRow] = []
    for upload in files:
        try:
            rows.extend(ingest_file(upload.filename or "", upload.file, session))
        finally:
            upload.file.close()
    latest_by_path = {row.rel_path: row.id for row in rows if row.status != "rejected"}
    latest_ids = set(latest_by_path.values())
    rejected_ids = {row.id for row in rows if row.status == "rejected"}
    replaced: list[str] = []
    response_rows: list[UploadRow] = []
    with session["discovery"]._work_lock, session["upload_lock"]:
        for row in rows:
            if row.status != "rejected":
                if row.id not in latest_ids:
                    session["uploads"].pop(row.id, None)
                    if row.store_path:
                        session["store"].delete(row.store_path)
                    continue
                for existing in list(session["uploads"].values()):
                    if (
                        existing.id != row.id
                        and existing.rel_path == row.rel_path
                        and existing.id not in rejected_ids
                    ):
                        removed = _remove_upload(request, session, existing.id)
                        if removed is not None:
                            replaced.append(removed.id)
            session["inventory"].add_upload(row)
            if row.message and row.status != "rejected":
                session["inventory"].add_issue(
                    row.id,
                    severity="warning",
                    code="upload.detect_warning",
                    message=row.message,
                )
            if row.status != "rejected" and row.store_path:
                session["discovery"].submit(row.id)
            response_rows.append(row)
    digest = hashlib.sha256("".join(row.digest for row in rows).encode()).hexdigest()
    request.app.state.audit_log.append(
        "user",
        "uploads.received",
        session["session_id"],
        {
            "count": len(rows),
            "size": sum(row.size for row in rows),
            "sha256": digest,
        },
    )
    return {
        "uploads": [_upload_dict(request, row) for row in response_rows],
        "replaced": replaced,
    }


@router.get("/uploads")
def get_uploads(request: Request) -> list[dict[str, object]]:
    session = _session(request)
    return [_upload_dict(request, row) for row in session.get("uploads", {}).values()]


@router.patch("/uploads/{upload_id}")
def patch_upload(upload_id: str, payload: UploadSelection, request: Request) -> dict[str, object]:
    session = _session(request)
    row = _row(request, upload_id)
    row.selected = payload.selected
    session["inventory"].set_selected(upload_id, payload.selected)
    return _upload_dict(request, row)


@router.delete("/uploads/{upload_id}")
def delete_upload(upload_id: str, request: Request) -> dict[str, bool]:
    session = _session(request)
    with session["discovery"]._work_lock, session["upload_lock"]:
        if _remove_upload(request, session, upload_id) is None:
            raise HTTPException(status_code=404, detail="Unknown upload.")
    return {"deleted": True}


@router.get("/discovery/status")
def get_discovery_status(request: Request) -> dict[str, Any]:
    return _session(request)["discovery"].status()
