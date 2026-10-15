from __future__ import annotations

from fastapi import APIRouter, Request

from migraai.core.config import APP_VERSION

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings_store.read()
    return {
        "status": "ready",
        "mode": "local",
        "version": APP_VERSION,
        "devin_configured": settings.devin_pat.configured,
    }
