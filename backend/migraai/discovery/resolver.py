from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from migraai.discovery import ast
from migraai.discovery.objects import ParsedObject


@dataclass(slots=True, frozen=True)
class DiscoveryEdge:
    source: str
    target: str
    kind: str
    external: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "external": self.external,
        }


def _name(parts: list[str], parser: Any) -> tuple[str, ...]:
    rule = str(getattr(parser, "case_rule", "insensitive")).casefold()
    if rule not in {"upper", "lower", "insensitive"}:
        rule = "insensitive"
    if rule == "upper":
        return tuple(part.upper() for part in parts)
    if rule == "lower":
        return tuple(part.lower() for part in parts)
    return tuple(part.casefold() for part in parts)


def _schema_value(node: ast.Node) -> str | None:
    if isinstance(node, ast.Use) and node.database.parts:
        return node.database.parts[-1].value
    if isinstance(node, ast.SetStatement):
        for item in node.items:
            left = item.name
            right = item.value
            left_name = ""
            if isinstance(left, ast.ColumnRef) and left.name.parts:
                left_name = left.name.parts[-1].value.casefold()
            elif isinstance(left, ast.Identifier):
                left_name = left.value.casefold()
            if left_name in {"schema", "database"}:
                if isinstance(right, ast.ColumnRef) and right.name.parts:
                    return right.name.parts[-1].value
                if isinstance(right, ast.Identifier):
                    return right.value
                if isinstance(right, ast.Literal):
                    return right.value
    return None


def resolve(
    objects: list[ParsedObject],
    source_dialect: str,
    *,
    object_files: dict[int, str] | None = None,
    statements_by_file: dict[str, list[ast.Node]] | None = None,
    dialects_by_file: dict[str, Any] | None = None,
) -> tuple[list[ParsedObject], list[DiscoveryEdge]]:
    object_files = object_files or {}
    statements_by_file = statements_by_file or {}
    dialects_by_file = dialects_by_file or {}
    fallback_parser: Any = None
    try:
        from migraai.discovery.registry import get_sql_parser

        fallback_parser = get_sql_parser(source_dialect)
    except Exception:
        fallback_parser = None
    if fallback_parser is None:
        from migraai.discovery.parser_base import ParserBase

        fallback_parser = ParserBase

    schemas: dict[str, str | None] = {}
    for path, statements in statements_by_file.items():
        parser = dialects_by_file.get(path, fallback_parser)
        value = None
        for statement in statements:
            for node in statement.walk():
                candidate = _schema_value(node)
                if candidate is not None:
                    value = candidate
        schemas[path] = value
    for item in objects:
        path = object_files.get(id(item), item.file_path or "")
        if path and path not in schemas and item.default_schema:
            schemas[path] = item.default_schema

    by_name: dict[tuple[str, ...], list[ParsedObject]] = defaultdict(list)
    parsers: dict[int, Any] = {}
    for item in objects:
        path = object_files.get(id(item), item.file_path or "")
        parser = dialects_by_file.get(path, fallback_parser)
        parsers[id(item)] = parser
        parts = item.qualified_name.split(".")
        by_name[_name(parts, parser)].append(item)
        by_name[_name([item.name], parser)].append(item)

    external: dict[tuple[str, ...], ParsedObject] = {}
    edges: list[DiscoveryEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for item in objects:
        parser = parsers[id(item)]
        path = object_files.get(id(item), item.file_path or "")
        default_schema = schemas.get(path)
        for reference in item.references:
            parts = [part.value for part in reference.name.parts]
            normalized = _name(parts, parser)
            candidates = list(by_name.get(normalized, []))
            if len(parts) == 1 and default_schema:
                schema_key = _name([default_schema, parts[0]], parser)
                in_schema = by_name.get(schema_key, [])
                if in_schema:
                    candidates = list(in_schema)
            candidates = list({id(candidate): candidate for candidate in candidates}.values())
            target: str
            is_external = False
            if len(candidates) == 1:
                target = candidates[0].qualified_name
            elif len(candidates) > 1:
                issue = ast.Issue(
                    "warning",
                    "ambiguous_reference",
                    f"Reference to {'.'.join(parts)} matches multiple local objects.",
                    reference.span,
                )
                item.issues.append(issue)
                target = ".".join(parts)
            else:
                target = ".".join(parts)
                is_external = True
                key = _name(parts, parser)
                if key not in external:
                    external[key] = ParsedObject(
                        type="external",
                        schema=".".join(parts[:-1]) or None,
                        name=parts[-1] if parts else target,
                        qualified_name=target,
                        start_line=reference.span.start_line,
                        end_line=reference.span.end_line,
                        status="external",
                        references=[],
                        issues=[],
                        node=ast.Unparsed(reason="External reference"),
                        file_path=path,
                    )
            edge_key = (item.qualified_name, target, reference.ref_kind)
            if edge_key not in seen:
                seen.add(edge_key)
                edges.append(
                    DiscoveryEdge(item.qualified_name, target, reference.ref_kind, is_external)
                )
    return [*objects, *external.values()], edges
