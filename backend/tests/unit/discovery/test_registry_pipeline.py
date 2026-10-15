from types import SimpleNamespace

from migraai.discovery import ast, pipeline, registry
from migraai.discovery.objects import ParsedObject
from migraai.discovery.parsers.ansi import Parser
from migraai.discovery.pipeline import discover_files


def test_lazy_registry_returns_none_for_missing_modules(monkeypatch) -> None:
    monkeypatch.setitem(registry.SQL_PARSERS, "missing", "migraai.discovery.parsers.no_such_parser")
    assert registry.get_sql_parser("missing") is None


def test_pipeline_reports_unsupported_sql_parser(monkeypatch) -> None:
    monkeypatch.setitem(registry.SQL_PARSERS, "ansi", "migraai.discovery.parsers.no_such_parser")
    objects, edges = discover_files({"a.sql": b"SELECT 1"}, "ansi")
    assert not edges
    assert len(objects) == 1
    assert objects[0].type == "unsupported"
    assert objects[0].issues[0].code == "registry.unsupported_type"


def test_pipeline_parses_embedded_sql_and_adds_etl_references(monkeypatch) -> None:
    owner = ParsedObject(
        type="python",
        schema=None,
        name="job",
        qualified_name="job",
        start_line=1,
        end_line=1,
        status="parsed",
        references=[],
        issues=[],
        node=ast.Unparsed(text="", reason="fixture"),
    )
    embedded = SimpleNamespace(
        owner_qualified_name="job",
        text="SELECT id FROM source_table; INSERT INTO target_table SELECT id FROM source_table",
        location="job.py:query",
        dialect_hint=None,
    )
    etl_result = SimpleNamespace(objects=[owner], embedded_sql=[embedded], issues=[])
    monkeypatch.setattr(pipeline, "get_etl_parser", lambda _tool: lambda _data, _path: etl_result)
    monkeypatch.setattr(pipeline, "get_sql_parser", lambda _dialect: Parser)

    objects, edges = discover_files({"job.py": b"pass"}, "ansi")
    assert owner in objects
    assert all(item.type == "external" for item in objects if item is not owner)
    assert any(reference.ref_kind == "etl_reads" for reference in owner.references)
    assert any(reference.ref_kind == "etl_writes" for reference in owner.references)
    assert {edge.kind for edge in edges} == {"etl_reads", "etl_writes"}
    assert all(edge.external for edge in edges)
