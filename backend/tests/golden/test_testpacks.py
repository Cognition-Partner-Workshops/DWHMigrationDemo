from pathlib import Path
from typing import Any

import pytest
import yaml

from migraai.discovery.pipeline import discover_files

ROOT = Path(__file__).resolve().parents[3] / "testpacks"


def _expected(pack: Path) -> dict[str, Any]:
    path = pack / "expected_inventory.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _pack_config(pack: Path) -> tuple[str, str]:
    config_path = pack / "pack.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    config = config or {}
    kind = str(config.get("kind", "sql")).casefold()
    source_dialect = config.get("source_dialect")
    if kind not in {"sql", "etl"}:
        raise AssertionError(f"Unsupported pack kind {kind!r} in {config_path}")
    if source_dialect is None:
        if kind == "etl":
            raise AssertionError(f"ETL pack {pack.name!r} requires source_dialect.")
        source_dialect = pack.name
    return kind, str(source_dialect)


def _pack_dirs() -> list[Path]:
    return (
        sorted(path.parent for path in ROOT.rglob("expected_inventory.yaml"))
        if ROOT.is_dir()
        else []
    )


def _pack_id(pack: Path) -> str:
    return pack.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("pack", _pack_dirs(), ids=_pack_id)
def test_testpack_inventory_matches_golden(pack: Path) -> None:
    _kind, source_dialect = _pack_config(pack)
    files = {
        path.relative_to(pack).as_posix(): path.read_bytes()
        for path in sorted(pack.rglob("*"))
        if path.is_file()
        and path.name not in {"pack.yaml", "expected_inventory.yaml"}
        and "golden" not in path.relative_to(pack).parts
    }
    objects, edges = discover_files(files, source_dialect)
    expected = _expected(pack)
    actual_objects = sorted(
        (
            {"type": item.type, "qualified_name": item.qualified_name, "status": item.status}
            for item in objects
        ),
        key=lambda item: (item["qualified_name"], item["type"], item["status"]),
    )
    actual_edges = sorted(
        (
            {
                "from": edge.source,
                "to": edge.target,
                "kind": edge.kind,
                "external": edge.external,
            }
            for edge in edges
        ),
        key=lambda item: (item["from"], item["to"], item["kind"], item["external"]),
    )
    expected_objects = sorted(
        expected.get("objects", []),
        key=lambda item: (item["qualified_name"], item["type"], item["status"]),
    )
    expected_edges = sorted(
        expected.get("edges", []),
        key=lambda item: (
            item["from"],
            item["to"],
            item["kind"],
            item.get("external", False),
        ),
    )
    assert actual_objects == expected_objects
    assert actual_edges == expected_edges


def test_nested_pack_discovery_finds_all_sql_and_etl_packs() -> None:
    pack_ids = [_pack_id(pack) for pack in _pack_dirs()]

    assert len(pack_ids) == 17
    assert sum("/" not in pack_id for pack_id in pack_ids) == 9
    assert sum(pack_id.startswith("etl/") for pack_id in pack_ids) == 8


def test_pack_configuration_defaults_and_requires_etl_source_dialect(tmp_path: Path) -> None:
    sql_pack = tmp_path / "sql_pack"
    sql_pack.mkdir()
    assert _pack_config(sql_pack) == ("sql", "sql_pack")

    etl_pack = tmp_path / "etl_pack"
    etl_pack.mkdir()
    (etl_pack / "pack.yaml").write_text("kind: etl\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="requires source_dialect"):
        _pack_config(etl_pack)

    (etl_pack / "pack.yaml").write_text("kind: etl\nsource_dialect: ansi\n", encoding="utf-8")
    assert _pack_config(etl_pack) == ("etl", "ansi")
