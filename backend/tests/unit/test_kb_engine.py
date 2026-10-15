import json
import shutil
from pathlib import Path

from migraai.core.audit import AuditLog
from migraai.kb.build import build_database
from migraai.kb.embed import HashEmbedder
from migraai.kb.search import hybrid_search
from migraai.kb.store import (
    KBStore,
    compute_seed_hash,
    connect,
    create_schema,
    insert_chunk,
    seed_database_hash,
)
from migraai.main import _initialize_kb


def test_build_empty_seed_database(tmp_path: Path) -> None:
    output = tmp_path / "kb-v1.db"
    build_database(tmp_path / "empty-seed", output, allow_incomplete=True, embedder=HashEmbedder())

    with connect(output) as connection:
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0


def test_hybrid_search_filters_profiles_and_escapes_fts(tmp_path: Path) -> None:
    store = KBStore(tmp_path)
    store.create_empty()
    database = store.current_path
    assert database is not None
    texts = [
        ("teradata data types", "teradata", None, None),
        ("snowflake data types", "snowflake", None, None),
        ("teradata snowflake data types", None, "teradata", "snowflake"),
        ("oracle data types", "oracle", None, None),
    ]
    vectors = HashEmbedder().embed([item[0] for item in texts])
    with connect(database) as connection:
        for (text, platform, source, target), vector in zip(texts, vectors, strict=True):
            insert_chunk(
                connection,
                (
                    None,
                    text,
                    "https://docs.example.test",
                    "2025-01-01",
                    platform,
                    source,
                    target,
                    "sql_dialect_differences",
                    None,
                    "high",
                ),
                vector,
            )

    hits = hybrid_search(
        "data types ) OR *",
        source="teradata",
        target="snowflake",
        k=8,
        db_path=database,
        embedder=HashEmbedder(),
    )

    assert {hit.platform for hit in hits if hit.source is None} == {"teradata", "snowflake"}
    assert any(hit.source == "teradata" and hit.target == "snowflake" for hit in hits)
    assert all(hit.platform != "oracle" for hit in hits)
    assert all(hit.score > 0 for hit in hits)


def test_hybrid_search_filters_one_thousand_vector_candidates_in_one_batch(
    tmp_path: Path,
) -> None:
    store = KBStore(tmp_path)
    store.create_empty()
    database = store.current_path
    assert database is not None
    embedder = HashEmbedder()
    query = "needle migration strategy"
    texts = [query] + [f"unrelated filler item {index}" for index in range(1000)]
    vectors = embedder.embed(texts)
    with connect(database) as connection:
        for index, (text, vector) in enumerate(zip(texts, vectors, strict=True)):
            source = "teradata" if index == 0 else "oracle"
            target = "snowflake" if index == 0 else None
            platform = None if index == 0 else "oracle"
            insert_chunk(
                connection,
                (
                    None,
                    text,
                    "https://docs.example.test",
                    "2025-01-01",
                    platform,
                    source if index == 0 else None,
                    target,
                    "strategy",
                    None,
                    "high",
                ),
                vector,
            )

    hits = hybrid_search(
        query,
        source="teradata",
        target="snowflake",
        db_path=database,
        embedder=embedder,
    )
    assert len(hits) == 1
    assert hits[0].text == query


def test_store_retains_three_versions_and_rolls_back(tmp_path: Path) -> None:
    store = KBStore(tmp_path)
    for version in range(1, 5):
        store.create_empty(version)

    assert store.versions() == [4, 3, 2]
    assert store.state_path.is_file()
    assert not list(store.root.glob("state-*.json"))
    assert store.rollback() == 3
    assert store.current_version == 3
    assert store.versions() == [3, 2]


def test_build_database_keeps_a_versioned_store_independent(tmp_path: Path) -> None:
    output = tmp_path / "build" / "kb-v1.db"
    build_database(tmp_path / "seed", output, allow_incomplete=True, embedder=HashEmbedder())
    copied = tmp_path / "copy.db"
    shutil.copy2(output, copied)
    assert copied.is_file()


def test_startup_seed_upgrade_preserves_refresh_data_and_audits(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    store = KBStore(data_root)
    current = store.create_empty()
    with connect(current) as connection:
        connection.execute(
            """INSERT INTO documents
            (id,url,title,fetched_at,platform,sha256,origin)
            VALUES('refresh-doc','https://docs.example.test/refresh','refresh','now',
                   'teradata','digest','refresh')"""
        )
        insert_chunk(
            connection,
            (
                "refresh-doc",
                "retained refreshed content",
                "https://docs.example.test/refresh",
                "2025-01-01",
                "teradata",
                None,
                None,
                "refresh",
                None,
                None,
            ),
            HashEmbedder().embed(["retained refreshed content"])[0],
        )

    bundled = tmp_path / "bundled.db"
    with connect(bundled) as connection:
        create_schema(connection)
        connection.execute(
            """INSERT INTO documents
            (id,url,title,fetched_at,platform,sha256,origin)
            VALUES('seed-doc','https://vendor.example.test/seed','seed','now',
                   'teradata','digest','seed')"""
        )
        insert_chunk(
            connection,
            (
                "seed-doc",
                "new seed content",
                "https://vendor.example.test/seed",
                "2025-01-01",
                "teradata",
                None,
                None,
                "architecture",
                None,
                "high",
            ),
            HashEmbedder().embed(["new seed content"])[0],
        )
        digest = compute_seed_hash(connection)
        connection.execute(
            """INSERT INTO kb_versions
            (version,created_at,documents,chunks,seed_sha256) VALUES(1,'now',1,1,?)""",
            (digest,),
        )

    audit = AuditLog(data_root / "logs" / "audit.jsonl")
    _initialize_kb(store, tmp_path / "empty-seed", bundled, audit)

    assert store.current_version == 2
    assert seed_database_hash(store.current_path) == digest
    with connect(store.current_path) as connection:
        assert {row["origin"] for row in connection.execute("SELECT origin FROM documents")} == {
            "seed",
            "refresh",
        }
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM chunks_vec").fetchone()[0] == 2
    events = [
        json.loads(line) for line in (data_root / "logs" / "audit.jsonl").read_text().splitlines()
    ]
    assert any(event["action"] == "kb.seed_upgrade" for event in events)
