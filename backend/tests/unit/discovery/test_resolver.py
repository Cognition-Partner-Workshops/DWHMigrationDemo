from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from migraai.discovery import ast
from migraai.discovery.objects import ParsedObject
from migraai.discovery.pipeline import discover_files
from migraai.discovery.references import Reference
from migraai.discovery.registry import ETL_PARSERS
from migraai.discovery.resolver import resolve


def _object(
    qualified_name: str,
    *,
    schema: str | None = None,
    refs: list[str] | None = None,
    path: str = "a.sql",
) -> ParsedObject:
    name = qualified_name.split(".")[-1]
    return ParsedObject(
        type="table",
        schema=schema,
        name=name,
        qualified_name=qualified_name,
        start_line=1,
        end_line=1,
        status="parsed",
        references=[
            Reference(
                ast.QualifiedName(parts=[ast.Identifier(value=part) for part in ref.split(".")]),
                "reads",
                ast.Span(),
            )
            for ref in refs or []
        ],
        issues=[],
        node=ast.Unparsed(),
        file_path=path,
    )


class UpperParser:
    case_rule = "upper"


def test_case_rule_and_unqualified_default_schema_resolution() -> None:
    source = _object("source", refs=["item"])
    target = _object("sales.item", schema="sales", path="b.sql")
    use = ast.Use(
        database=ast.QualifiedName(parts=[ast.Identifier(value="sales")]),
    )

    objects, edges = resolve(
        [source, target],
        "ansi",
        object_files={id(source): "a.sql", id(target): "b.sql"},
        statements_by_file={"a.sql": [use]},
        dialects_by_file={"a.sql": UpperParser, "b.sql": UpperParser},
    )

    assert edges[0].target == "sales.item"
    assert not edges[0].external
    assert len(objects) == 2


def test_ambiguous_and_external_references_are_reported() -> None:
    source = _object("source", refs=["duplicate", "missing"])
    first = _object("one.duplicate", schema="one", path="b.sql")
    second = _object("two.duplicate", schema="two", path="c.sql")

    objects, edges = resolve(
        [source, first, second],
        "ansi",
        object_files={id(source): "a.sql", id(first): "b.sql", id(second): "c.sql"},
    )

    assert any(issue.code == "ambiguous_reference" for issue in source.issues)
    assert any(item.type == "external" and item.name == "missing" for item in objects)
    assert any(edge.target == "missing" and edge.external for edge in edges)


def test_fake_etl_parser_embedded_sql_attaches_reads_and_writes(monkeypatch) -> None:
    module_name = "test_fake_etl_parser"
    module = types.ModuleType(module_name)

    def parse(data: bytes, path: str):
        owner = _object("job", path=path)
        embedded = SimpleNamespace(
            owner_qualified_name="job",
            text="insert into target select * from source",
            location="embedded",
            dialect_hint="ansi",
        )
        return SimpleNamespace(objects=[owner], embedded_sql=[embedded], issues=[])

    module.parse = parse
    monkeypatch.setitem(sys.modules, module_name, module)
    monkeypatch.setitem(ETL_PARSERS, "python", module_name)

    objects, _ = discover_files({"job.py": b"print('not executed')"}, "ansi")

    owner = next(item for item in objects if item.qualified_name == "job")
    assert {reference.ref_kind for reference in owner.references} >= {"etl_reads", "etl_writes"}
