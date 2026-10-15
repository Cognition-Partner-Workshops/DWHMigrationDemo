from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from migraai.discovery import ast
from migraai.discovery.detect import decode_source, detect_file
from migraai.discovery.objects import ParsedObject
from migraai.discovery.objects import extract as extract_objects
from migraai.discovery.references import Reference
from migraai.discovery.registry import get_etl_parser, get_sql_parser
from migraai.discovery.resolver import DiscoveryEdge, _schema_value, resolve


def _unsupported(rel_path: str, source_dialect: str, kind: str) -> ParsedObject:
    issue = ast.Issue(
        "warning",
        "registry.unsupported_type",
        f"Unsupported {kind} parser for {rel_path!r} (source dialect {source_dialect!r}).",
    )
    node = ast.Unparsed(text="", reason="Unsupported type")
    return ParsedObject(
        type="unsupported",
        schema=None,
        name=PurePosixPath(rel_path.replace("\\", "/")).name,
        qualified_name=rel_path.replace("\\", "/"),
        start_line=1,
        end_line=1,
        status="unparsed",
        references=[],
        issues=[issue],
        node=node,
        file_path=rel_path,
    )


def discover_files(
    files: dict[str, bytes], source_dialect: str
) -> tuple[list[ParsedObject], list[DiscoveryEdge]]:
    objects: list[ParsedObject] = []
    object_files: dict[int, str] = {}
    statements_by_file: dict[str, list[ast.Node]] = {}
    dialects_by_file: dict[str, Any] = {}
    for rel_path, data in files.items():
        detection = detect_file(rel_path, data, source_dialect)
        if detection.parser_type == "sql":
            parser_type = get_sql_parser(detection.dialect or source_dialect)
            if parser_type is None:
                objects.append(_unsupported(rel_path, source_dialect, "SQL"))
                continue
            text, _, _ = decode_source(data)
            parser = parser_type()
            result = parser.parse(text, rel_path)
            file_objects = extract_objects(result, rel_path, parser_type)
            default_schema = None
            for statement in result.statements:
                for node in statement.walk():
                    candidate = _schema_value(node)
                    if candidate is not None:
                        default_schema = candidate
            for item in file_objects:
                item.default_schema = default_schema
            objects.extend(file_objects)
            object_files.update({id(item): rel_path for item in file_objects})
            statements_by_file[rel_path] = result.statements
            dialects_by_file[rel_path] = parser_type
            continue
        detected_tool = detection.parser_type
        parser = get_etl_parser(detected_tool) if detected_tool else None
        if parser is None:
            objects.append(_unsupported(rel_path, source_dialect, detection.detected_type))
            continue
        result = parser(data, rel_path)
        etl_objects = list(getattr(result, "objects", []))
        for etl_object in etl_objects:
            etl_object.file_path = rel_path
        objects.extend(etl_objects)
        object_files.update({id(item): rel_path for item in etl_objects})
        etl_issues = list(getattr(result, "issues", []))
        for etl_object in etl_objects:
            etl_object.issues.extend(etl_issues)
        for embedded in getattr(result, "embedded_sql", []):
            hint = getattr(embedded, "dialect_hint", None)
            embedded_dialect = (
                hint if hint and get_sql_parser(str(hint)) is not None else source_dialect
            )
            sql_parser_type = get_sql_parser(embedded_dialect)
            if sql_parser_type is None:
                owner_name = getattr(embedded, "owner_qualified_name", "")
                if isinstance(owner_name, ast.QualifiedName):
                    owner_name = ".".join(part.value for part in owner_name.parts)
                owner = next(
                    (item for item in etl_objects if item.qualified_name == str(owner_name)),
                    None,
                )
                if owner is not None:
                    owner.issues.append(
                        ast.Issue(
                            "warning",
                            "registry.unsupported_embedded_sql",
                            f"Unsupported SQL dialect {embedded_dialect!r} for embedded SQL.",
                        )
                    )
                continue
            sql_parser = sql_parser_type()
            parsed_sql = sql_parser.parse(
                str(getattr(embedded, "text", "")),
                str(getattr(embedded, "location", rel_path)),
            )
            sql_objects = extract_objects(
                parsed_sql,
                str(getattr(embedded, "location", rel_path)),
                sql_parser_type,
            )
            owner_name = getattr(embedded, "owner_qualified_name", "")
            if isinstance(owner_name, ast.QualifiedName):
                owner_name = ".".join(part.value for part in owner_name.parts)
            owner = next(
                (item for item in etl_objects if item.qualified_name == str(owner_name)),
                None,
            )
            if owner is not None:
                for sql_object in sql_objects:
                    for reference in sql_object.references:
                        ref_kind = {
                            "reads": "etl_reads",
                            "writes": "etl_writes",
                        }.get(reference.ref_kind)
                        if ref_kind is not None:
                            owner.references.append(
                                Reference(reference.name, ref_kind, reference.span)
                            )
        dialects_by_file[rel_path] = get_sql_parser(source_dialect)
    return resolve(
        objects,
        source_dialect,
        object_files=object_files,
        statements_by_file=statements_by_file,
        dialects_by_file=dialects_by_file,
    )


__all__ = ["DiscoveryEdge", "discover_files"]
