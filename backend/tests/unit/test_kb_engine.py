import shutil
from pathlib import Path

from migraai.kb.build import build_database
from migraai.kb.embed import HashEmbedder
from migraai.kb.search import hybrid_search
from migraai.kb.store import KBStore, connect, insert_chunk


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
