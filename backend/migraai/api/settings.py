from __future__ import annotations

from fastapi import APIRouter, Request

from migraai.core.audit import AuditLog
from migraai.core.settings_store import SettingsRead, SettingsUpdate

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=SettingsRead)
def get_settings(request: Request) -> SettingsRead:
    return request.app.state.settings_store.read()


@router.put("/settings", response_model=SettingsRead)
def update_settings(settings: SettingsUpdate, request: Request) -> SettingsRead:
    changed = request.app.state.settings_store.update(settings)
    audit: AuditLog = request.app.state.audit_log
    audit.append("user", "settings.update", "app-settings", {"fields": sorted(changed)})
    return request.app.state.settings_store.read()
