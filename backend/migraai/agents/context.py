from __future__ import annotations

import importlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

import sqlglot

from migraai.devin.attachment import render_batch
from migraai.discovery import ast
from migraai.discovery.detect import decode_source
from migraai.discovery.objects import ParsedObject
from migraai.discovery.objects import extract as extract_objects
from migraai.discovery.pipeline import discover_files
from migraai.discovery.registry import get_sql_parser
from migraai.etl.python.common import redact
from migraai.kb.search import get_mappings, get_pair
from migraai.platforms.pack import TargetPack
from migraai.validation.dialects import sqlglot_dialect

from .batching import Batch

_LANGUAGE_BY_SUFFIX = {
    ".ddl": "sql",
    ".sql": "sql",
    ".txt": "sql",
    ".py": "python",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}
_SOURCE_DIALECTS = {
    "teradata": "teradata",
    "oracle": "oracle",
    "sqlserver": "tsql",
    "synapse": "tsql",
    "snowflake": "snowflake",
    "hive": "hive",
    "spark": "spark",
}
_SQL_SECRET_PATTERNS = (
    re.compile(r"""(?i)(\bPASSWORD\s+)(['"])(?:\2\2|(?!\2).)*\2"""),
    re.compile(r"""(?i)(\bIDENTIFIED\s+BY\s+)(['"])(?:\2\2|(?!\2).)*\2"""),
)


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _row_id(row: Any) -> str:
    return str(_row_value(row, "id", _row_value(row, "object_id", "")))


def _qualified_name(row: Any) -> str:
    return str(_row_value(row, "qualified_name", _row_value(row, "name", "")))


def _mask_source(text: str) -> str:
    text = redact(text)

    def mask(match: re.Match[str]) -> str:
        quote = match.group(2)
        return f"{match.group(1)}{quote}***{quote}"

    for pattern in _SQL_SECRET_PATTERNS:
        text = pattern.sub(mask, text)
    return re.sub(r"(?i)(\bIDENTIFIED\s+BY\s+)\S+", r"\1***", text)


def _source_for(session: dict[str, Any], row: Any, upload_by_path: dict[str, Any]) -> str:
    file_path = str(_row_value(row, "file", ""))
    upload = upload_by_path.get(file_path)
    if upload is None:
        return str(_row_value(row, "source_preview", ""))
    data = session["store"].read(upload.store_path)
    text, _, _ = decode_source(data)
    lines = text.splitlines()
    start = max(1, int(_row_value(row, "start_line", 1) or 1))
    end = max(start, int(_row_value(row, "end_line", start) or start))
    return "\n".join(lines[start - 1 : end])


def _inventory_objects(session: dict[str, Any]) -> list[dict[str, Any]]:
    inventory = session["inventory"]
    result: list[dict[str, Any]] = []
    page = 1
    while True:
        current = inventory.page(page=page, page_size=300)["items"]
        result.extend(current)
        if len(current) < 300:
            return result
        page += 1


def _parsed_objects(
    session: dict[str, Any],
    source: str,
    rows: list[dict[str, Any]],
    upload_by_path: dict[str, Any],
) -> dict[str, ParsedObject]:
    by_path: dict[str, bytes] = {}
    for row in rows:
        file_path = str(row.get("file", ""))
        upload = upload_by_path.get(file_path)
        if upload is None or file_path in by_path:
            continue
        by_path[file_path] = session["store"].read(upload.store_path)
    parsed_by_key: dict[str, ParsedObject] = {}
    for file_path, data in by_path.items():
        try:
            parser_type = get_sql_parser(source)
            if parser_type is not None:
                text, _, _ = decode_source(data)
                parser = parser_type()
                result = parser.parse(text, file_path)
                objects = extract_objects(result, file_path, parser_type)
            else:
                objects, _ = discover_files({file_path: data}, source)
        except Exception:
            continue
        for item in objects:
            if item.status == "external":
                continue
            key = f"{file_path}\0{item.qualified_name}\0{item.start_line}"
            parsed_by_key[key] = item
    return parsed_by_key


def _find_parsed(row: dict[str, Any], parsed: dict[str, ParsedObject]) -> ParsedObject | None:
    file_path = str(row.get("file", ""))
    name = str(row.get("qualified_name", ""))
    start = int(row.get("start_line", 1) or 1)
    exact = parsed.get(f"{file_path}\0{name}\0{start}")
    if exact is not None:
        return exact
    return next(
        (
            item
            for item in parsed.values()
            if item.file_path == file_path
            and item.qualified_name == name
            and item.start_line <= start <= max(item.end_line, item.start_line)
        ),
        None,
    )


def _node_summary(node: ast.Node | None) -> dict[str, Any]:
    if node is None:
        return {"statement_kinds": [], "dialect_constructs": [], "columns": []}
    statement_nodes = node.statements if isinstance(node, ast.Block) else [node]
    dialect_constructs = []
    columns = []
    for child in node.walk():
        if isinstance(child, (ast.DialectExpr, ast.DialectStatement)):
            dialect_constructs.append(
                {
                    "kind": child.kind,
                    "name": getattr(child, "name", None),
                    "dialect": getattr(child, "dialect", None),
                }
            )
        elif isinstance(child, ast.ColumnDef):
            columns.append(
                {
                    "name": child.name.value,
                    "type": (
                        child.type.name
                        if isinstance(child.type.name, str)
                        else ".".join(part.value for part in child.type.name.parts)
                    ),
                }
            )
    return {
        "statement_kinds": [statement.kind for statement in statement_nodes],
        "dialect_constructs": dialect_constructs,
        "columns": columns,
    }


def _references(parsed: ParsedObject | None) -> list[dict[str, str]]:
    if parsed is None:
        return []
    return [
        {
            "name": ".".join(part.value for part in reference.name.parts),
            "kind": reference.ref_kind,
        }
        for reference in parsed.references
    ]


def _dependencies(session: dict[str, Any], object_id: str) -> list[dict[str, Any]]:
    detail = session["inventory"].detail(object_id)
    if detail is None:
        return []
    dependencies = []
    for edge in detail.get("upstream", []):
        name = edge.get("qualified_name") or edge.get("id")
        if name:
            dependencies.append(
                {
                    "name": str(name),
                    "external": bool(edge.get("external")),
                    "kind": str(edge.get("kind", "")),
                }
            )
    return dependencies


def _columns_from_parsed(parsed: ParsedObject | None) -> list[dict[str, str]]:
    if parsed is None:
        return []
    return _node_summary(parsed.node)["columns"]


def _mapping_lines(
    source: str,
    target: str,
    texts: list[str],
    db_path: Any,
) -> list[str]:
    joined = "\n".join(texts)
    by_kind: dict[str, dict[str, dict[str, Any]]] = {"type": {}, "function": {}}
    try:
        for platform in (source, target):
            for mapping in get_mappings(platform, db_path=db_path):
                kind = str(mapping.get("kind", ""))
                if kind not in by_kind:
                    continue
                by_kind[kind].setdefault(str(mapping["canonical"]), {})[platform] = mapping
    except Exception:
        return []
    lines = []
    for kind in ("type", "function"):
        for canonical, mappings in sorted(by_kind[kind].items()):
            source_mapping = mappings.get(source)
            if (
                source_mapping is None
                or re.search(
                    rf"(?<![\w$]){re.escape(str(source_mapping['native']))}(?![\w$])",
                    joined,
                    re.IGNORECASE,
                )
                is None
            ):
                continue
            target_mapping = mappings.get(target)
            target_native = target_mapping["native"] if target_mapping else "no direct equivalent"
            lines.append(
                f"- {kind}: {source_mapping['native']} → {canonical} → {target_native}"
                + (
                    f" ({target_mapping['notes']})"
                    if target_mapping and target_mapping["notes"]
                    else ""
                )
            )
    return lines


def _pair_notes(source: str, target: str, db_path: Any) -> str:
    try:
        value = get_pair(source, target, db_path=db_path).get("sql_dialect_differences", [])
    except Exception:
        return ""
    if isinstance(value, list):
        return "\n".join(
            "- "
            + (
                f"{item.get('text', '')} ({item.get('url', '')})"
                if isinstance(item, dict)
                else str(item)
            )
            for item in value
        )
    return str(value)


def _format_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        result = dict(value)
    elif hasattr(value, "model_dump"):
        result = value.model_dump(mode="json")
    elif is_dataclass(value):
        result = asdict(value)
    else:
        result = {
            name: getattr(value, name)
            for name in ("id", "rel_path", "language", "description", "skeleton")
            if hasattr(value, name)
        }
    for key in ("rel_path", "path"):
        path = result.get(key)
        if isinstance(path, str) and (
            PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()
        ):
            result[key] = PureWindowsPath(path).name if "\\" in path else PurePosixPath(path).name
    return result


def _etl_formats(target: str) -> list[dict[str, Any]]:
    try:
        module = importlib.import_module("migraai.etl.outputs")
        formats = module.formats_for(target)
    except (ImportError, AttributeError, ModuleNotFoundError):
        return []
    return [_format_dict(item) for item in formats]


def _transpile_hint(text: str, source: str, target: str) -> str | None:
    source_dialect = _SOURCE_DIALECTS.get(source.casefold())
    if source_dialect is None:
        return None
    try:
        converted = sqlglot.transpile(
            text,
            read=source_dialect,
            write=sqlglot_dialect(target),
        )
    except Exception:
        return None
    return "\n".join(converted) if converted else None


def build_batch(
    session: dict[str, Any],
    batch: Batch,
    source: str,
    target: str,
    pack: TargetPack,
    name_map: dict[str, str],
) -> tuple[str, list[dict[str, Any]]]:
    inventory_rows = _inventory_objects(session)
    row_by_id = {str(row["id"]): row for row in inventory_rows}
    batch_rows = [
        row_by_id.get(_row_id(item), item if isinstance(item, dict) else {})
        for item in batch.objects
    ]
    selected_uploads = {
        upload.rel_path: upload
        for upload in session.get("uploads", {}).values()
        if getattr(upload, "selected", True)
    }
    parsed = _parsed_objects(session, source, inventory_rows, selected_uploads)
    parsed_by_name = {item.qualified_name: item for item in parsed.values()}
    batch_names = {_qualified_name(row) for row in batch_rows}
    object_context: list[dict[str, Any]] = []
    conversion_objects: list[dict[str, Any]] = []
    source_texts: list[str] = []
    dependency_context: dict[str, dict[str, Any]] = {}
    hints: list[str] = []

    for row in batch_rows:
        source_text = _mask_source(_source_for(session, row, selected_uploads))
        source_texts.append(source_text)
        object_id = str(row["id"])
        qualified_name = str(row.get("qualified_name", ""))
        parsed_object = _find_parsed(row, parsed)
        dependencies = _dependencies(session, object_id)
        dependency_names = [item["name"] for item in dependencies]
        dependency_signatures = []
        for dependency in dependencies:
            name = dependency["name"]
            if name in batch_names:
                continue
            dependency_object = parsed_by_name.get(name)
            signature = {
                "name": name,
                "external": dependency["external"],
                "columns": _columns_from_parsed(dependency_object),
            }
            dependency_signatures.append(signature)
            dependency_context[name] = signature
        summary = _node_summary(parsed_object.node if parsed_object else None)
        object_type = str(row.get("type", "object"))
        text_extension = "." + str(row.get("file", "")).rsplit(".", 1)[-1].casefold()
        source_language = _LANGUAGE_BY_SUFFIX.get(text_extension)
        file_formats = (
            _etl_formats(target)
            if source_language is None
            or str(row.get("type", "")).casefold() in {"workflow", "mapping", "session", "worklet"}
            else []
        )
        language = source_language or next(
            (
                str(item.get("language", ""))
                for item in file_formats
                if item.get("language") in {"sql", "python", "json", "yaml"}
            ),
            "python",
        )
        item_context = {
            "object_id": object_id,
            "rel_path": str(row.get("file", "")),
            "language": language,
            "object_type": object_type,
            "status": str(row.get("status", "")),
            "target_name": name_map.get(qualified_name, qualified_name),
            "dependencies": dependency_names,
            "dependency_signatures": dependency_signatures,
            "summary": summary,
            "references": _references(parsed_object),
            "output_formats": file_formats,
            "source_text": source_text,
        }
        object_context.append(item_context)
        conversion_objects.append(
            {
                "object_id": object_id,
                "source_qualified_name": qualified_name,
                "object_type": object_type,
                "dependencies": dependency_names,
                "source_columns": summary["columns"],
                "source_text": source_text,
                "rel_path": str(row.get("file", "")),
                "language": language,
                "output_formats": file_formats,
                "node": parsed_object.node if parsed_object else None,
                "row": row,
            }
        )
        if language == "sql":
            hint = _transpile_hint(source_text, source, target)
            if hint:
                hints.append(f"### {qualified_name}\n```sql\n{hint}\n```")

    db_path = session.get("kb_path")
    mappings = _mapping_lines(source, target, source_texts, db_path)
    pair_notes = _pair_notes(source, target, db_path)
    naming_lines = [
        f"- {name} → {target_name}"
        for name, target_name in sorted(name_map.items())
        if name in {_qualified_name(row) for row in batch_rows}
        or name in batch.external_dependencies
    ]
    rules_lines = [
        f"- `{rule.id}` ({rule.check}): {rule.message}"
        + (f" Replacement: {rule.replacement}" if rule.replacement else "")
        for rule in pack.rules
    ]
    sections = [
        ("Target guidance", pack.guidance),
        ("Target rules", "\n".join(rules_lines)),
        ("Naming map", "\n".join(naming_lines) or "No mapped identifiers."),
        (
            "Dependency signatures",
            json.dumps(dependency_context, ensure_ascii=False, sort_keys=True),
        ),
        ("Type and function mappings", "\n".join(mappings) or "No matching KB mappings."),
        ("Source-to-target dialect notes", pair_notes or "No pair-specific dialect notes."),
    ]
    formats = {
        json.dumps(output_format, ensure_ascii=False, sort_keys=True)
        for item_context in object_context
        for output_format in item_context["output_formats"]
    }
    if formats:
        sections.append(("ETL target formats", "\n".join(sorted(formats))))
    if hints:
        sections.append(("Optional SQLGlot pre-transpile hints", "\n\n".join(hints)))
    attachment = render_batch(object_context, sections=sections)
    return attachment, conversion_objects


__all__ = ["build_batch"]
