from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
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
          documents INTEGER NOT NULL DEFAULT 0, chunks INTEGER NOT NULL DEFAULT 0,
          seed_sha256 TEXT NOT NULL DEFAULT ''
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
        CREATE TABLE IF NOT EXISTS platform_hosts (
          platform TEXT NOT NULL, host TEXT NOT NULL, PRIMARY KEY(platform,host)
        );
        """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(kb_versions)")}
    if "seed_sha256" not in columns:
        connection.execute(
            "ALTER TABLE kb_versions ADD COLUMN seed_sha256 TEXT NOT NULL DEFAULT ''"
        )
    connection.commit()


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


def compute_seed_hash(connection: sqlite3.Connection) -> str:
    tables = (
        (
            "documents",
            """SELECT url,title,platform,sha256,etag,last_modified FROM documents
            WHERE origin='seed' ORDER BY url,title""",
        ),
        (
            "chunks",
            """SELECT c.text,c.url,c.accessed,c.platform,c.source,c.target,c.section,
            c.object_type,c.claim_confidence FROM chunks c
            JOIN documents d ON d.id=c.document_id WHERE d.origin='seed'
            ORDER BY c.url,c.section,c.text""",
        ),
        (
            "claims",
            """SELECT text,url,accessed,applies_to,confidence,platform,source,target,section,
            object_type
            FROM claims ORDER BY url,section,text""",
        ),
        (
            "mappings",
            "SELECT platform,kind,native,canonical,notes,url FROM mappings ORDER BY 1,2,3",
        ),
        (
            "catalog_rows",
            """SELECT source,tier,id,name,description,object_types,extraction_hint
            FROM catalog_rows ORDER BY 1,2,3""",
        ),
        ("pair_text", "SELECT source,target,section,value FROM pair_text ORDER BY 1,2,3"),
        ("platform_hosts", "SELECT platform,host FROM platform_hosts ORDER BY 1,2"),
    )
    digest = hashlib.sha256()
    for table, query in tables:
        digest.update(table.encode("utf-8") + b"\0")
        for row in connection.execute(query):
            digest.update(
                json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            digest.update(b"\n")
    return digest.hexdigest()


def seed_database_hash(database: Path) -> str:
    with closing(connect(database)) as connection:
        create_schema(connection)
        row = connection.execute(
            "SELECT version,seed_sha256 FROM kb_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return compute_seed_hash(connection)
        if row["seed_sha256"]:
            return str(row["seed_sha256"])
        digest = compute_seed_hash(connection)
        connection.execute(
            "UPDATE kb_versions SET seed_sha256=? WHERE version=?", (digest, row["version"])
        )
        connection.commit()
        return digest


def upgrade_seed_database(
    seed_database: Path,
    current_database: Path,
    output_database: Path,
    version: int,
) -> Path:
    output_database.parent.mkdir(parents=True, exist_ok=True)
    output_database.unlink(missing_ok=True)
    with closing(connect(seed_database)) as seed, closing(connect(output_database)) as output:
        seed.backup(output)
        create_schema(output)
    with closing(connect(current_database)) as current, closing(connect(output_database)) as output:
        for row in current.execute("SELECT * FROM documents WHERE origin='refresh'"):
            output.execute(
                """INSERT INTO documents
                (id,url,title,fetched_at,platform,sha256,etag,last_modified,origin)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                tuple(row),
            )
        next_chunk_id = int(
            output.execute("SELECT COALESCE(MAX(id),0)+1 FROM chunks").fetchone()[0]
        )
        refresh_chunks = current.execute(
            """SELECT c.*,v.embedding FROM chunks c
            JOIN documents d ON d.id=c.document_id
            JOIN chunks_vec v ON v.rowid=c.id
            WHERE d.origin='refresh' ORDER BY c.id"""
        ).fetchall()
        for row in refresh_chunks:
            chunk_id = next_chunk_id
            next_chunk_id += 1
            output.execute(
                """INSERT INTO chunks
                (id,document_id,text,url,accessed,platform,source,target,section,object_type,
                 claim_confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    chunk_id,
                    row["document_id"],
                    row["text"],
                    row["url"],
                    row["accessed"],
                    row["platform"],
                    row["source"],
                    row["target"],
                    row["section"],
                    row["object_type"],
                    row["claim_confidence"],
                ),
            )
            output.execute(
                "INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (chunk_id, row["text"])
            )
            output.execute(
                "INSERT INTO chunks_vec(rowid,embedding) VALUES(?,?)",
                (chunk_id, bytes(row["embedding"])),
            )
        document_count = int(output.execute("SELECT count(*) FROM documents").fetchone()[0])
        chunk_count = int(output.execute("SELECT count(*) FROM chunks").fetchone()[0])
        seed_hash = compute_seed_hash(output)
        output.execute("DELETE FROM kb_versions")
        output.execute(
            """INSERT INTO kb_versions(version,created_at,documents,chunks,seed_sha256)
            VALUES(?,?,?,?,?)""",
            (version, datetime.now(UTC).isoformat(), document_count, chunk_count, seed_hash),
        )
        output.commit()
    return output_database


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

    @property
    def current_seed_hash(self) -> str | None:
        path = self.current_path
        return seed_database_hash(path) if path else None

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
        with closing(connect(path)) as connection:
            create_schema(connection)
            seed_hash = compute_seed_hash(connection)
            connection.execute(
                """INSERT OR REPLACE INTO kb_versions
                (version,created_at,documents,chunks,seed_sha256) VALUES(?,?,?,?,?)""",
                (version, datetime.now(UTC).isoformat(), 0, 0, seed_hash),
            )
            connection.commit()
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
