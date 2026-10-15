from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from migraai.core.secrets import SecretProtector

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "devin_org_id": "",
    "devin_api_base_url": "https://api.devin.ai",
    "confidence_threshold": 0.85,
    "max_concurrent_sessions": 4,
    "acu_cap_per_run": None,
}


class SecretStatus(BaseModel):
    configured: bool
    masked: str


class SettingsRead(BaseModel):
    devin_pat: SecretStatus
    devin_org_id: str
    devin_api_base_url: str
    confidence_threshold: float
    max_concurrent_sessions: int
    acu_cap_per_run: float | None


class SettingsUpdate(BaseModel):
    devin_pat: str | None = None
    devin_org_id: str | None = None
    devin_api_base_url: str | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.5, le=1.0)
    max_concurrent_sessions: int | None = Field(default=None, ge=1, le=50)
    acu_cap_per_run: float | None = Field(default=None, gt=0)


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
        if blob is None:
            secret = SecretStatus(configured=False, masked="")
        else:
            try:
                pat = self.protector.unprotect(blob).decode("utf-8")
                secret = SecretStatus(configured=True, masked=f"••••{pat[-4:]}")
            except Exception:
                logger.warning("Could not read protected Devin token.")
                secret = SecretStatus(configured=True, masked="••••")

        result: dict[str, Any] = {"devin_pat": secret}
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
            else:
                result[key] = raw
        return SettingsRead.model_validate(result)

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
