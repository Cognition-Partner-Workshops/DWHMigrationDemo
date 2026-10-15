from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlite_vec

VECTOR_DIM = 384


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
    except Exception as error:
        connection.close()
        raise RuntimeError(
            "Could not load the sqlite-vec extension; install the matching sqlite-vec wheel."
        ) from error
    finally:
        try:
            connection.enable_load_extension(False)
        except sqlite3.Error:
            pass
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS kb_versions (
          version INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
          documents INTEGER NOT NULL DEFAULT 0, chunks INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
          fetched_at TEXT NOT NULL, platform TEXT, sha256 TEXT NOT NULL,
          etag TEXT, last_modified TEXT, origin TEXT NOT NULL CHECK(origin IN ('seed','refresh'))
        );
        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY, document_id TEXT, text TEXT NOT NULL, url TEXT NOT NULL,
          accessed TEXT, platform TEXT, source TEXT, target TEXT, section TEXT,
          object_type TEXT, claim_confidence TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
          text, content='chunks', content_rowid='id'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[384]);
        CREATE TABLE IF NOT EXISTS claims (
          id TEXT PRIMARY KEY, text TEXT NOT NULL, url TEXT NOT NULL, accessed TEXT NOT NULL,
          applies_to TEXT, confidence TEXT NOT NULL, platform TEXT, source TEXT, target TEXT,
          section TEXT, object_type TEXT
        );
        CREATE TABLE IF NOT EXISTS mappings (
          platform TEXT NOT NULL, kind TEXT NOT NULL, native TEXT NOT NULL,
          canonical TEXT NOT NULL, notes TEXT NOT NULL, url TEXT NOT NULL,
          PRIMARY KEY(platform, kind, native)
        );
        CREATE TABLE IF NOT EXISTS catalog_rows (
          source TEXT NOT NULL, tier TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
          description TEXT NOT NULL, object_types TEXT NOT NULL, extraction_hint TEXT NOT NULL,
          PRIMARY KEY(source, tier, id)
        );
        CREATE TABLE IF NOT EXISTS pair_text (
          source TEXT NOT NULL, target TEXT NOT NULL, section TEXT NOT NULL, value TEXT NOT NULL,
          PRIMARY KEY(source, target, section)
        );
        """
    )


def insert_chunk(
    connection: sqlite3.Connection,
    values: tuple[Any, ...],
    embedding: list[float],
) -> int:
    cursor = connection.execute(
        """INSERT INTO chunks
        (document_id,text,url,accessed,platform,source,target,section,object_type,claim_confidence)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        values,
    )
    chunk_id = int(cursor.lastrowid)
    connection.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (chunk_id, values[1]))
    connection.execute(
        "INSERT INTO chunks_vec(rowid,embedding) VALUES(?,?)",
        (chunk_id, sqlite_vec.serialize_float32(embedding)),
    )
    return chunk_id


class KBStore:
    def __init__(self, data_root: Path) -> None:
        self.root = Path(data_root) / "kb"
        self.state_path = self.root / "state.json"

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    @property
    def current_version(self) -> int | None:
        value = self.state().get("current_version")
        return int(value) if value is not None else None

    @property
    def current_path(self) -> Path | None:
        version = self.current_version
        path = self.root / f"kb-v{version}.db" if version is not None else None
        return path if path and path.is_file() else None

    def version_path(self, version: int) -> Path:
        return self.root / f"kb-v{version}.db"

    def versions(self) -> list[int]:
        result = []
        for path in self.root.glob("kb-v*.db"):
            try:
                result.append(int(path.stem.removeprefix("kb-v")))
            except ValueError:
                continue
        return sorted(result, reverse=True)

    def activate(self, version: int, summary: dict[str, Any] | None = None) -> None:
        self.initialize()
        old_state = self.state()
        state = {
            "current_version": version,
            "last_refresh_summary": (
                summary if summary is not None else old_state.get("last_refresh_summary")
            ),
        }
        descriptor, name = tempfile.mkstemp(prefix="state-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.state_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        retained = {version, version - 1, version - 2}
        for old_version in self.versions():
            if old_version not in retained:
                self.version_path(old_version).unlink(missing_ok=True)

    def create_empty(self, version: int = 1) -> Path:
        self.initialize()
        path = self.version_path(version)
        with connect(path) as connection:
            create_schema(connection)
            connection.execute(
                "INSERT OR REPLACE INTO kb_versions(version,created_at) VALUES(?,?)",
                (version, datetime.now(UTC).isoformat()),
            )
        self.activate(version)
        return path

    def rollback(self) -> int | None:
        current = self.current_version
        candidates = [
            version for version in self.versions() if current is not None and version < current
        ]
        if not candidates:
            return None
        self.activate(max(candidates))
        return max(candidates)
