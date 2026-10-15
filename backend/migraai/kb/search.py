from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from migraai.core.config import get_data_root
from migraai.kb.embed import Embedder, get_embedder
from migraai.kb.store import KBStore, connect


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    text: str
    url: str
    accessed: str | None
    platform: str | None
    source: str | None
    target: str | None
    section: str | None
    score: float


def _filters(
    source: str | None, target: str | None, section: str | None, object_type: str | None
) -> tuple[str, list[Any]]:
    pair_clauses, pair_values = [], []
    if source:
        pair_clauses.append("c.source=?")
        pair_values.append(source)
    if target:
        pair_clauses.append("c.target=?")
        pair_values.append(target)
    clauses, values = [], []
    if pair_clauses:
        profiles = []
        if source:
            profiles.append("c.platform=?")
            values.append(source)
        if target and target != source:
            profiles.append("c.platform=?")
            values.append(target)
        profile_sql = (
            "(c.source IS NULL AND c.target IS NULL AND (" + " OR ".join(profiles) + "))"
            if profiles
            else "0"
        )
        clauses.append("((" + " AND ".join(pair_clauses) + ") OR " + profile_sql + ")")
        values = pair_values + values
    if section:
        clauses.append("c.section=?")
        values.append(section)
    if object_type:
        clauses.append("c.object_type=?")
        values.append(object_type)
    return (" AND ".join(clauses) if clauses else "1=1"), values


def hybrid_search(
    query: str,
    *,
    source: str | None = None,
    target: str | None = None,
    section: str | None = None,
    object_type: str | None = None,
    k: int = 8,
    db_path: Path | None = None,
    embedder: Embedder | None = None,
) -> list[Hit]:
    if db_path is None:
        raise ValueError("db_path is required.")
    words = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not words:
        return []
    fts_query = " OR ".join(f'"{word.replace(chr(34), chr(34) * 2)}"' for word in words)
    where, params = _filters(source, target, section, object_type)
    with connect(db_path) as connection:
        lexical = connection.execute(
            f"""SELECT c.*, bm25(chunks_fts) AS rank FROM chunks_fts
            JOIN chunks c ON c.id=chunks_fts.rowid
            WHERE chunks_fts MATCH ? AND {where}
            ORDER BY rank LIMIT 50""",
            (fts_query, *params),
        ).fetchall()
        vector = (embedder or get_embedder()).embed([query])[0]
        total_chunks = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
        neighbor_limit = min(total_chunks, 1000)
        neighbors = (
            connection.execute(
                "SELECT rowid,distance FROM chunks_vec WHERE embedding MATCH ? AND k = ?",
                (json.dumps(vector), neighbor_limit),
            ).fetchall()
            if neighbor_limit
            else []
        )
        vector_scores: dict[int, int] = {}
        candidate_ids = [int(neighbor["rowid"]) for neighbor in neighbors]
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            matching_ids = {
                int(row["id"])
                for row in connection.execute(
                    f"SELECT c.id FROM chunks c WHERE c.id IN ({placeholders}) AND {where}",
                    (*candidate_ids, *params),
                ).fetchall()
            }
            for neighbor in neighbors:
                chunk_id = int(neighbor["rowid"])
                if chunk_id in matching_ids:
                    vector_scores[chunk_id] = len(vector_scores) + 1
                    if len(vector_scores) == 50:
                        break
        ids: dict[int, tuple[int | None, int | None]] = {}
        for rank, row in enumerate(lexical, 1):
            ids[int(row["id"])] = (rank, None)
        for chunk_id, rank in vector_scores.items():
            lexical_rank, _ = ids.get(chunk_id, (None, None))
            ids[chunk_id] = (lexical_rank, rank)
        chunks = {}
        if ids:
            placeholders = ",".join("?" for _ in ids)
            chunks = {
                int(row["id"]): row
                for row in connection.execute(
                    f"SELECT * FROM chunks WHERE id IN ({placeholders})", tuple(ids)
                ).fetchall()
            }
    results = []
    for chunk_id, (lexical_rank, vector_rank) in ids.items():
        score = (1 / (60 + lexical_rank) if lexical_rank else 0) + (
            1 / (60 + vector_rank) if vector_rank else 0
        )
        row = chunks[chunk_id]
        results.append(
            Hit(
                chunk_id,
                row["text"],
                row["url"],
                row["accessed"],
                row["platform"],
                row["source"],
                row["target"],
                row["section"],
                score,
            )
        )
    return sorted(results, key=lambda hit: hit.score, reverse=True)[:k]


def _current_db(path: Path | None) -> Path:
    if path:
        return path
    db = KBStore(get_data_root()).current_path
    if db is None:
        raise RuntimeError("Knowledge base has not been initialized.")
    return db


def get_catalog(source: str, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    with connect(_current_db(db_path)) as connection:
        rows = connection.execute(
            "SELECT * FROM catalog_rows WHERE source=? ORDER BY tier,id", (source,)
        )
        result = []
        for row in rows:
            item = dict(row)
            item["object_types"] = json.loads(item["object_types"])
            result.append(item)
        return result


def get_mappings(platform: str, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    with connect(_current_db(db_path)) as connection:
        rows = connection.execute(
            "SELECT * FROM mappings WHERE platform=? ORDER BY kind,native", (platform,)
        )
        return [dict(row) for row in rows]


def get_pair(source: str, target: str, *, db_path: Path | None = None) -> dict[str, Any]:
    with connect(_current_db(db_path)) as connection:
        rows = connection.execute(
            "SELECT section,value FROM pair_text WHERE source=? AND target=?", (source, target)
        )
        return {row["section"]: json.loads(row["value"]) for row in rows}
