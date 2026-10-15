from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from migraai.discovery import ast
from migraai.discovery.objects import ParsedObject
from migraai.discovery.objects import extract as extract_objects
from migraai.discovery.references import Reference
from migraai.discovery.registry import get_etl_parser, get_sql_parser


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


_ETL_EXTENSIONS = {
    ".dtsx": "ssis",
    ".dsx": "datastage",
    ".xml": "informatica",
    ".py": "python",
    ".sh": "shell",
    ".bash": "shell",
    ".yml": "airflow",
    ".yaml": "airflow",
    ".json": "spark",
}


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
    )


def _detect(rel_path: str, data: bytes) -> tuple[str, str]:
    suffix = PurePosixPath(rel_path.replace("\\", "/")).suffix.casefold()
    if suffix == ".sql":
        return "sql", ""
    return "etl", _ETL_EXTENSIONS.get(suffix, "")


def discover_files(
    files: dict[str, bytes], source_dialect: str
) -> tuple[list[ParsedObject], list[DiscoveryEdge]]:
    objects: list[ParsedObject] = []
    for rel_path, data in files.items():
        file_type, detected_tool = _detect(rel_path, data)
        if file_type == "sql":
            parser_type = get_sql_parser(source_dialect)
            if parser_type is None:
                objects.append(_unsupported(rel_path, source_dialect, file_type))
                continue
            text = data.decode("utf-8-sig", errors="replace")
            parser = parser_type()
            result = parser.parse(text, rel_path)
            objects.extend(extract_objects(result, rel_path, parser_type))
            continue
        parser = get_etl_parser(detected_tool) if detected_tool else None
        if parser is None:
            objects.append(_unsupported(rel_path, source_dialect, file_type))
            continue
        result = parser(data, rel_path)
        etl_objects = list(getattr(result, "objects", []))
        objects.extend(etl_objects)
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

    names: dict[str, int] = {}
    for item in objects:
        names[item.qualified_name] = names.get(item.qualified_name, 0) + 1
    edges: list[DiscoveryEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for item in objects:
        for reference in item.references:
            target = _reference_name(reference)
            key = (item.qualified_name, target, reference.ref_kind)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                DiscoveryEdge(
                    source=item.qualified_name,
                    target=target,
                    kind=reference.ref_kind,
                    external=names.get(target, 0) != 1,
                )
            )
    return objects, edges


def _reference_name(reference: Reference) -> str:
    return ".".join(part.value for part in reference.name.parts)


__all__ = ["DiscoveryEdge", "discover_files"]
