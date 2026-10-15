from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from migraai.kb.search import get_catalog

router = APIRouter(prefix="/api/resources", tags=["resources"])


class ResourceUpdate(BaseModel):
    available: bool


def _session(request: Request) -> dict:
    session = request.app.state.current_session
    if not session:
        raise HTTPException(status_code=404, detail="No confirmed source platform.")
    return session


def _availability(session: dict) -> dict[str, bool]:
    try:
        return json.loads(session["store"].read("resources.json"))
    except FileNotFoundError:
        return {}


@router.get("")
def list_resources(request: Request) -> list[dict]:
    session = _session(request)
    rows = get_catalog(session["source"], db_path=request.app.state.kb_store.current_path)
    available = _availability(session)
    return [
        {
            **row,
            "available": bool(available.get(str(row["id"]), False)),
            "count": None,
            "status": "Awaiting upload",
        }
        for row in rows
    ]


@router.patch("/{resource_id}")
def update_resource(resource_id: str, payload: ResourceUpdate, request: Request) -> dict:
    session = _session(request)
    rows = get_catalog(session["source"], db_path=request.app.state.kb_store.current_path)
    if not any(str(row["id"]) == resource_id for row in rows):
        raise HTTPException(status_code=404, detail="Unknown catalog resource.")
    available = _availability(session)
    available[resource_id] = payload.available
    session["store"].write("resources.json", json.dumps(available).encode("utf-8"))
    request.app.state.audit_log.append(
        "user",
        "resources.update",
        session["session_id"],
        {"resource_id": resource_id, "available": payload.available},
    )
    return {"id": resource_id, "available": payload.available}
