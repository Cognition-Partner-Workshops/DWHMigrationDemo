from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api", tags=["discovery"])


def _inventory(request: Request):
    session = request.app.state.current_session
    if not session:
        raise HTTPException(status_code=409, detail="Confirm a source and target first.")
    return session["inventory"]


@router.get("/inventory")
def get_inventory(
    request: Request,
    q: str = "",
    object_type: str | None = Query(default=None, alias="type"),
    status: str | None = None,
    file: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=300),
) -> dict[str, Any]:
    return _inventory(request).page(
        query=q,
        object_type=object_type,
        status=status,
        file=file,
        page=page,
        page_size=page_size,
    )


@router.get("/inventory/{object_id}")
def get_inventory_detail(object_id: str, request: Request) -> dict[str, Any]:
    item = _inventory(request).detail(object_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown inventory object.")
    item["source_preview"] = "\n".join(item.get("source_preview", "").splitlines()[:200])
    return item


@router.get("/graph")
def get_graph(
    request: Request,
    object_id: str,
    depth: int = Query(default=1, ge=1, le=3),
) -> dict[str, Any]:
    result = _inventory(request).graph(object_id, depth)
    if not any(node["id"] == object_id for node in result["nodes"]):
        raise HTTPException(status_code=404, detail="Unknown inventory object.")
    return result
