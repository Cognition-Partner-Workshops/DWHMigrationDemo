from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from migraai.discovery import ast
from migraai.discovery.references import Reference
from migraai.discovery.references import extract as extract_references


@dataclass(slots=True)
class ParsedObject:
    type: str
    schema: str | None
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    status: str
    references: list[Reference]
    issues: list[ast.Issue]
    node: ast.Node

    def to_dict(self, *, include_node: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.type,
            "schema": self.schema,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "status": self.status,
            "references": [reference.to_dict() for reference in self.references],
            "issues": [issue.to_dict() for issue in self.issues],
        }
        if include_node:
            result["node"] = self.node.to_dict()
        return result


_CREATE_TYPES: dict[type[ast.Node], str] = {
    ast.CreateTable: "table",
    ast.CreateView: "view",
    ast.CreateIndex: "index",
    ast.CreateSequence: "sequence",
    ast.CreateSynonym: "synonym",
    ast.CreateSchema: "schema",
    ast.CreateDatabase: "database",
}


def _qualified(value: ast.QualifiedName) -> tuple[str | None, str, str]:
    names = [part.value for part in value.parts]
    if not names:
        return None, "", ""
    return (names[-2] if len(names) > 1 else None, names[-1], ".".join(names))


def _definition(node: ast.Node) -> tuple[str, ast.QualifiedName] | None:
    for cls, object_type in _CREATE_TYPES.items():
        if isinstance(node, cls):
            if isinstance(node, ast.CreateTable) and node.external:
                object_type = "external_table"
            elif isinstance(node, ast.CreateView) and node.materialized:
                object_type = "materialized_view"
            return object_type, node.name
    if isinstance(node, ast.CreateRoutine):
        return node.routine_kind.casefold(), node.name
    if isinstance(node, ast.CreatePrincipal):
        return node.principal_kind.casefold(), node.name
    if isinstance(node, ast.Grant):
        label = f"{', '.join(node.privileges)} on {_qualified(node.object_name)[2]}".strip()
        return "grant", ast.QualifiedName(parts=[ast.Identifier(value=label)])
    if isinstance(node, ast.DialectStatement):
        object_type = node.args.get("object_type")
        name = node.args.get("name") or node.args.get("object_name")
        if object_type and name:
            if isinstance(name, ast.QualifiedName):
                qualified_name = name
            elif isinstance(name, ast.Identifier):
                qualified_name = ast.QualifiedName(parts=[name])
            else:
                qualified_name = ast.QualifiedName(
                    parts=[ast.Identifier(value=part) for part in str(name).split(".") if part]
                )
            return str(object_type), qualified_name
    return None


def _status(node: ast.Node, issues: list[ast.Issue]) -> str:
    if isinstance(node, ast.Unparsed):
        return "unparsed"
    if any(isinstance(child, ast.Unparsed) for child in node.walk()) or any(
        issue.severity == "error" for issue in issues
    ):
        return "partial"
    return "parsed"


def _object(
    node: ast.Node, object_type: str, name: ast.QualifiedName, issues: list[ast.Issue], dialect: Any
) -> ParsedObject:
    schema, object_name, qualified_name = _qualified(name)
    span = node.span
    return ParsedObject(
        type=object_type,
        schema=schema,
        name=object_name,
        qualified_name=qualified_name,
        start_line=span.start_line,
        end_line=span.end_line,
        status=_status(node, issues),
        references=extract_references(node, dialect),
        issues=issues,
        node=node,
    )


def extract(result: ast.ParseResult, rel_path: str, dialect: Any = None) -> list[ParsedObject]:
    definitions: list[ParsedObject] = []
    for statement in result.statements:
        definition = _definition(statement)
        if definition is None:
            continue
        object_type, name = definition
        span = statement.span
        issues = [
            issue
            for issue in result.issues
            if issue.span is None
            or span.start_line <= issue.span.start_line <= max(span.end_line, span.start_line)
        ]
        definitions.append(_object(statement, object_type, name, issues, dialect))
    if definitions:
        return definitions
    if not result.statements:
        return []
    end_line = max(1, result.statements[-1].span.end_line)
    script = ast.Block(
        statements=result.statements,
        span=ast.Span(1, 1, end_line, 1),
    )
    stem = PurePosixPath(rel_path.replace("\\", "/")).stem or rel_path
    return [
        ParsedObject(
            type="script",
            schema=None,
            name=stem,
            qualified_name=rel_path.replace("\\", "/"),
            start_line=1,
            end_line=script.span.end_line,
            status=_status(script, result.issues),
            references=extract_references(script, dialect),
            issues=list(result.issues),
            node=script,
        )
    ]
