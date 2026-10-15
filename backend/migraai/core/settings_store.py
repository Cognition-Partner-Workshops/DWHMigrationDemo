from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from migraai.core.secrets import SecretProtector
from migraai.devin.client import normalize_base_url

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "devin_org_id": "",
    "devin_api_base_url": "https://api.devin.ai",
    "confidence_threshold": 0.85,
    "max_concurrent_sessions": 4,
    "acu_cap_per_run": None,
    "plan_acu_cap": 10,
    "plan_timeout_minutes": 30,
    "conversion_batch_size": 20,
    "conversion_session_timeout_minutes": 45,
}


class SecretStatus(BaseModel):
    configured: bool
    masked: str


class SettingsRead(BaseModel):
    devin_pat: SecretStatus
    token_kind: str
    devin_org_id: str
    devin_api_base_url: str
    confidence_threshold: float
    max_concurrent_sessions: int
    acu_cap_per_run: float | None
    plan_acu_cap: int
    plan_timeout_minutes: int
    conversion_batch_size: int
    conversion_session_timeout_minutes: int


class SettingsUpdate(BaseModel):
    devin_pat: str | None = None
    devin_org_id: str | None = None
    devin_api_base_url: str | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.5, le=1.0)
    max_concurrent_sessions: int | None = Field(default=None, ge=1, le=50)
    acu_cap_per_run: float | None = Field(default=None, gt=0)
    plan_acu_cap: int | None = Field(default=None, ge=1, le=100)
    plan_timeout_minutes: int | None = Field(default=None, ge=5, le=120)
    conversion_batch_size: int | None = Field(default=None, ge=1, le=50)
    conversion_session_timeout_minutes: int | None = Field(default=None, ge=5, le=240)


class SettingsStore:
    def __init__(self, data_root: Path, protector: SecretProtector) -> None:
        self.data_root = data_root
        self.path = data_root / "app.db"
        self.protector = protector

    def initialize(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value BLOB)"
            )

    def _values(self) -> dict[str, bytes]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        return {str(key): bytes(value) for key, value in rows}

    def read(self) -> SettingsRead:
        values = self._values()
        blob = values.get("devin_pat")
        token_kind = "none"
        if blob is None:
            secret = SecretStatus(configured=False, masked="")
        else:
            try:
                pat = self.protector.unprotect(blob).decode("utf-8")
                secret = SecretStatus(configured=True, masked=f"••••{pat[-4:]}")
                if pat.startswith("apk_"):
                    token_kind = "legacy"
                elif pat.startswith("cog_"):
                    token_kind = "v3"
            except Exception:
                logger.warning("Could not read protected Devin token.")
                secret = SecretStatus(configured=True, masked="••••")
                token_kind = "none"

        result: dict[str, Any] = {"devin_pat": secret, "token_kind": token_kind}
        for key, default in DEFAULTS.items():
            if key not in values:
                result[key] = default
                continue
            raw = values[key].decode("utf-8")
            if key == "confidence_threshold":
                result[key] = float(raw)
            elif key == "max_concurrent_sessions":
                result[key] = int(raw)
            elif key == "acu_cap_per_run":
                result[key] = float(raw) if raw else None
            elif key in {
                "plan_acu_cap",
                "plan_timeout_minutes",
                "conversion_batch_size",
                "conversion_session_timeout_minutes",
            }:
                result[key] = int(raw)
            else:
                result[key] = raw
        return SettingsRead.model_validate(result)

    def runtime_devin_config(self) -> tuple[str, str, str]:
        values = self._values()
        blob = values.get("devin_pat")
        pat = self.protector.unprotect(blob).decode("utf-8") if blob else ""
        org_id = values.get("devin_org_id", b"").decode("utf-8")
        base_url = values.get("devin_api_base_url", b"https://api.devin.ai").decode("utf-8")
        return org_id, normalize_base_url(base_url), pat

    def update(self, settings: SettingsUpdate) -> set[str]:
        changed: set[str] = set()
        with sqlite3.connect(self.path) as connection:
            for key in settings.model_fields_set:
                value = getattr(settings, key)
                if key == "devin_pat":
                    if value:
                        stored = self.protector.protect(value.encode("utf-8"))
                        connection.execute(
                            "INSERT INTO settings(key, value) VALUES(?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, stored),
                        )
                    else:
                        connection.execute("DELETE FROM settings WHERE key = ?", (key,))
                    changed.add(key)
                    continue
                if value is None and key != "acu_cap_per_run":
                    continue
                stored_value = "" if value is None else str(value)
                connection.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, stored_value.encode("utf-8")),
                )
                changed.add(key)
        return changed
