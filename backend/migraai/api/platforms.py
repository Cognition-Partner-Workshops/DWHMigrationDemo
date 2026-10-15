from __future__ import annotations

from fastapi import APIRouter

from migraai.registry.platforms import SOURCES, TARGETS

router = APIRouter(prefix="/api", tags=["platforms"])


@router.get("/platforms")
def platforms() -> dict[str, list[dict[str, str]]]:
    return {"sources": SOURCES, "targets": TARGETS}
