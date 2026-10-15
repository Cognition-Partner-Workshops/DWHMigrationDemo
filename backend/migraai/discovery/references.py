from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from migraai.discovery import ast
from migraai.discovery.parser_base import ParserBase


@dataclass(slots=True)
class Reference:
    name: ast.QualifiedName
    ref_kind: str
    span: ast.Span

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.to_dict(),
            "ref_kind": self.ref_kind,
            "span": self.span.to_dict(),
        }


def _name_key(name: ast.QualifiedName) -> str:
    return ".".join(part.value if part.quoted else part.value.casefold() for part in name.parts)


def _name(value: ast.QualifiedName | str) -> ast.QualifiedName:
    if isinstance(value, ast.QualifiedName):
        return value
    return ast.QualifiedName(
        parts=[ast.Identifier(value=part) for part in value.split(".") if part]
    )


def extract(node: ast.Node, dialect: Any = None) -> list[Reference]:
    parser = (
        dialect
        if isinstance(dialect, type)
        else type(dialect)
        if dialect is not None
        else ParserBase
    )
    builtins = {
        str(name).casefold()
        for name in getattr(parser, "builtin_functions", ParserBase.ansi_builtins)
    }
    refs: list[Reference] = []

    def add(name: ast.QualifiedName | str | None, ref_kind: str, span: ast.Span) -> None:
        if name is not None:
            refs.append(Reference(_name(name), ref_kind, span))

    def visit(current: Any, ctes: frozenset[str] = frozenset()) -> None:
        if current is None or isinstance(current, (str, int, float, bool)):
            return
        if isinstance(current, (list, tuple)):
            for item in current:
                visit(item, ctes)
            return
        if isinstance(current, dict):
            for item in current.values():
                visit(item, ctes)
            return
        if not isinstance(current, ast.Node):
            return

        if isinstance(current, ast.Query):
            local_ctes = set(ctes)
            if current.with_:
                local_ctes.update(
                    _name_key(ast.QualifiedName(parts=[item.name])) for item in current.with_.ctes
                )
                for cte in current.with_.ctes:
                    visit(cte.query, frozenset(local_ctes))
            visit(current.body, frozenset(local_ctes))
            visit(current.order_by, frozenset(local_ctes))
            visit(current.limit, frozenset(local_ctes))
            return
        if isinstance(current, ast.TableRef):
            key = _name_key(current.name)
            final = current.name.parts[-1].value.casefold() if current.name.parts else ""
            if key not in ctes and final not in ctes:
                add(current.name, "reads", current.span)
            visit(current.temporal, ctes)
            visit(current.sample, ctes)
            return
        if isinstance(current, ast.CreateTable):
            if current.as_query is not None:
                add(current.name, "writes", current.span)
            add(current.like, "reads", current.span)
            visit(current.as_query, ctes)
            for column in current.columns:
                for constraint in column.constraints:
                    if constraint.constraint_kind == "foreign_key":
                        add(constraint.ref_table, "foreign_key", constraint.span)
            for constraint in current.constraints:
                if constraint.constraint_kind == "foreign_key":
                    add(constraint.ref_table, "foreign_key", constraint.span)
            return
        if isinstance(current, (ast.Insert, ast.Update, ast.Delete, ast.Merge, ast.Truncate)):
            add(current.target, "writes", current.span)
            if isinstance(current, ast.Insert):
                visit(current.source, ctes)
                visit(current.conflict, ctes)
            elif isinstance(current, ast.Update):
                visit(current.assignments, ctes)
                visit(current.from_, ctes)
                visit(current.where, ctes)
            elif isinstance(current, ast.Delete):
                visit(current.using, ctes)
                visit(current.where, ctes)
            elif isinstance(current, ast.Merge):
                visit(current.source, ctes)
                visit(current.on, ctes)
                visit(current.clauses, ctes)
            return
        if isinstance(current, ast.CreateView):
            visit(current.query, ctes)
            return
        if isinstance(current, ast.CreateRoutine):
            if current.routine_kind.casefold() == "trigger":
                add(current.options.get("trigger_on"), "trigger_on", current.span)
            if current.routine_kind.casefold() == "package_body":
                add(current.name, "defined_in", current.span)
            visit(current.body, ctes)
            return
        if isinstance(current, ast.CreateSynonym):
            add(current.target, "synonym_for", current.span)
            return
        if isinstance(current, ast.CreateIndex):
            add(current.table, "index_on", current.span)
            return
        if isinstance(current, ast.Grant):
            add(current.object_name, "grant_on", current.span)
            return
        if isinstance(current, ast.Revoke):
            add(current.object_name, "grant_on", current.span)
            return
        if isinstance(current, ast.Call):
            add(current.name, "calls", current.span)
            visit(current.args, ctes)
            return
        if isinstance(current, ast.DynamicSql):
            visit(current.parsed, ctes)
            visit(current.using, ctes)
            return
        if isinstance(current, ast.FunctionCall):
            function_name = _name_key(current.name).split(".")[-1]
            if function_name not in builtins:
                add(current.name, "calls", current.span)
            visit(current.args, ctes)
            visit(current.filter, ctes)
            visit(current.within_group, ctes)
            visit(current.over, ctes)
            return
        if isinstance(current, ast.DialectExpr):
            for hint in current.ref_hints:
                add(hint.name, hint.ref_kind, hint.span)
            visit(current.args, ctes)
            return
        if isinstance(current, ast.DialectStatement):
            for hint in current.references:
                add(hint.name, hint.ref_kind, hint.span)
            visit(current.args, ctes)
            return
        if isinstance(current, ast.Select):
            for item in current.from_:
                visit(item, ctes)
            for item in fields(current):
                if item.name not in {"span", "from_"}:
                    visit(getattr(current, item.name), ctes)
            return
        if isinstance(current, (ast.SubqueryRef, ast.TableFunctionRef)):
            for item in fields(current):
                if item.name != "span":
                    visit(getattr(current, item.name), ctes)
            return
        for item in fields(current):
            if item.name != "span":
                visit(getattr(current, item.name), ctes)

    visit(node)
    return refs
