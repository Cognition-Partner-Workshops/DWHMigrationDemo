from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from migraai.planning.schema import Plan


class PlanStore:
    def __init__(self, database: Path) -> None:
        self.database = Path(database)

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,target TEXT NOT NULL,kb_version INTEGER NOT NULL,
                origin TEXT NOT NULL CHECK(origin IN ('devin','local_draft')),
                devin_session_id TEXT,devin_url TEXT,reason TEXT,created_at TEXT NOT NULL,
                payload TEXT NOT NULL)"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS plans_pair_version "
                "ON plans(source,target,kb_version,id)"
            )

    def save(
        self,
        plan: Plan,
        *,
        source: str,
        target: str,
        kb_version: int,
        origin: str,
        devin_session_id: str | None = None,
        devin_url: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        payload = plan.model_dump(mode="json")
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                """INSERT INTO plans
                (source,target,kb_version,origin,devin_session_id,devin_url,reason,created_at,payload)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    source,
                    target,
                    kb_version,
                    origin,
                    devin_session_id,
                    devin_url,
                    reason,
                    datetime.now(UTC).isoformat(),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            plan_id = int(cursor.lastrowid)
        return self.get(plan_id)

    def get(self, plan_id: int) -> dict[str, Any]:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise KeyError(plan_id)
        record = dict(row)
        record["payload"] = json.loads(record["payload"])
        return record

    def latest(
        self,
        source: str,
        target: str,
        *,
        kb_version: int | None = None,
        origin: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["source=?", "target=?"]
        params: list[Any] = [source, target]
        if kb_version is not None:
            clauses.append("kb_version=?")
            params.append(kb_version)
        if origin is not None:
            clauses.append("origin=?")
            params.append(origin)
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                f"SELECT * FROM plans WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT 1",
                params,
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result
