"""Shared Python-source analysis for the python and spark ETL parsers.

Walks a (possibly recovered) ``ast.Module`` and emits ``module`` and
``function`` objects, ``calls`` references, ``etl_reads``/``etl_writes`` table
references and ``EmbeddedSql`` entries. Spark installs extra call handlers.
"""

from __future__ import annotations

import ast as pyast
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from migraai.discovery import ast
from migraai.etl.python.common import (
    EtlBuilder,
    attach_recovered,
    qname,
    span,
)
from migraai.etl.python.pyconvert import (
    ConvertContext,
    convert_expr,
    convert_statement,
    resolve_string,
)

_SECRET_NAME_RE = re.compile(r"password|passwd|pwd|secret|token|api_key", re.IGNORECASE)

THIRD_PARTY_TOP = frozenset(
    {
        "pandas",
        "numpy",
        "sqlalchemy",
        "pyspark",
        "airflow",
        "psycopg2",
        "pyodbc",
        "cx_Oracle",
        "oracledb",
        "teradatasql",
        "snowflake",
        "ibm_db",
        "pymssql",
        "pymysql",
        "boto3",
        "requests",
        "yaml",
        "dotenv",
        "delta",
        "py4j",
    }
)

SQL_METHODS = {"execute", "executemany", "executescript", "callproc"}
READ_SQL_METHODS = {"read_sql", "read_sql_query", "read_sql_table"}

_BARE_IDENT = __import__("re").compile(r"^[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*)*$")

CallHandler = Callable[[pyast.Call, "Scope", str, "EtlBuilder"], bool]


def module_name(rel_path: str) -> str:
    path = rel_path.replace("\\", "/")
    if path.endswith(".py"):
        path = path[:-3]
    parts = [part for part in path.split("/") if part]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or path


@dataclass(slots=True)
class Scope:
    """One lexical scope (module or function) while walking."""

    owner: str
    qual_prefix: str
    constants: dict[str, str] = field(default_factory=dict)
    local_defs: dict[str, str] = field(default_factory=dict)
    hints: list[ast.RefHint] = field(default_factory=list)
    calls: list[ast.Node] = field(default_factory=list)
    issues: list[ast.Issue] = field(default_factory=list)
    function_objects: list[tuple[str, pyast.stmt]] = field(default_factory=list)


class ModuleAnalyzer:
    def __init__(
        self,
        builder: EtlBuilder,
        rel_path: str,
        *,
        call_handler: CallHandler | None = None,
        emit_module_statements: bool = True,
    ) -> None:
        self.builder = builder
        self.tool = builder.tool
        self.rel_path = rel_path
        self.module_qname = module_name(rel_path)
        self.call_handler = call_handler
        self.emit_module_statements = emit_module_statements
        self.imports: dict[str, str] = {}
        self.ctx = ConvertContext(tool=self.tool)
        self._package = self.module_qname.rpartition(".")[0]

    # -- name tables -----------------------------------------------------

    def _collect_module_names(self, tree: pyast.Module, scope: Scope) -> None:
        for stmt in tree.body:
            if isinstance(stmt, (pyast.FunctionDef, pyast.AsyncFunctionDef, pyast.ClassDef)):
                scope.local_defs[stmt.name] = f"{self.module_qname}.{stmt.name}"
            elif isinstance(stmt, pyast.Assign):
                self._collect_constants(stmt, scope)
            elif isinstance(stmt, pyast.AnnAssign) and stmt.value is not None:
                if isinstance(stmt.target, pyast.Name):
                    value = resolve_string(stmt.value, scope.constants)
                    if value is not None:
                        scope.constants[stmt.target.id] = value
            elif isinstance(stmt, pyast.Import):
                for alias in stmt.names:
                    self.imports[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(stmt, pyast.ImportFrom):
                base = self._resolve_import_base(stmt)
                for alias in stmt.names:
                    if alias.name == "*":
                        continue
                    target = f"{base}.{alias.name}" if base else alias.name
                    self.imports[alias.asname or alias.name] = target

    def _collect_constants(self, stmt: pyast.Assign, scope: Scope) -> None:
        value = resolve_string(stmt.value, scope.constants)
        if value is None:
            return
        for target in stmt.targets:
            if isinstance(target, pyast.Name):
                if _SECRET_NAME_RE.search(target.id):
                    value = "****"
                scope.constants[target.id] = value

    def _resolve_import_base(self, stmt: pyast.ImportFrom) -> str:
        module = stmt.module or ""
        if stmt.level:
            parts = self.module_qname.split(".")
            keep = max(len(parts) - stmt.level, 0)
            base = ".".join(parts[:keep])
            return f"{base}.{module}" if module else base
        return module

    # -- main walk ---------------------------------------------------------

    def analyze(
        self,
        tree: pyast.Module | None,
        recovered: list[ast.Unparsed],
        recovered_issues: list[ast.Issue] | None = None,
    ) -> None:
        module_scope = Scope(owner=self.module_qname, qual_prefix=self.module_qname)
        statements: list[ast.Node] = []
        if tree is not None:
            self._collect_module_names(tree, module_scope)
            for stmt in tree.body:
                self._process_stmt(stmt, module_scope, top_level=True)
            if self.emit_module_statements:
                for stmt in tree.body:
                    if not isinstance(
                        stmt, (pyast.FunctionDef, pyast.AsyncFunctionDef, pyast.ClassDef)
                    ):
                        statements.append(convert_statement(stmt, self.ctx))
        module_args: dict[str, object] = {
            "name": qname(self.module_qname),
            "imports": [
                ast.Identifier(value=f"{alias}={target}")
                for alias, target in sorted(self.imports.items())
            ],
            "calls": module_scope.calls,
        }
        if statements:
            module_args["statements"] = statements
        end_line = max(
            [s.span.end_line for s in statements] + [r.span.end_line for r in recovered],
            default=1,
        )
        node = ast.DialectStatement(
            name=f"{self.tool}.module",
            args=module_args,
            references=module_scope.hints,
            span=span(1, end_line),
        )
        issues = module_scope.issues + self.ctx.issues
        base = len(self.builder.objects)
        module_obj = self.builder.add(
            "module",
            self.module_qname,
            node,
            1,
            node.span.end_line,
            issues=issues,
        )
        for qualified, fn_node in module_scope.function_objects:
            self._emit_function(qualified, fn_node, module_scope)
        leftover = attach_recovered(self.builder.objects[base:], recovered, recovered_issues)
        if leftover:
            args = getattr(module_obj.node, "args", None)
            if isinstance(args, dict):
                args.setdefault("recovered", []).extend(leftover)
            module_obj.status = "partial"

    def _emit_function(self, qualified: str, fn: pyast.stmt, base: Scope) -> None:
        assert isinstance(fn, (pyast.FunctionDef, pyast.AsyncFunctionDef))
        scope = Scope(
            owner=qualified,
            qual_prefix=qualified,
            constants=dict(base.constants),
            local_defs=dict(base.local_defs),
        )
        # collect function-local constants and nested defs
        for stmt in fn.body:
            if isinstance(stmt, pyast.Assign):
                self._collect_constants(stmt, scope)
            if isinstance(stmt, (pyast.FunctionDef, pyast.AsyncFunctionDef, pyast.ClassDef)):
                scope.local_defs[stmt.name] = f"{qualified}.{stmt.name}"
        for stmt in fn.body:
            self._process_stmt(stmt, scope)
        start, end = _fn_span(fn)
        decorators = [convert_expr(dec, self.ctx) for dec in fn.decorator_list]
        params = self._params(fn.args, scope)
        node = ast.DialectStatement(
            name=f"{self.tool}.function",
            args={
                "name": ast.Identifier(value=fn.name),
                "params": params,
                "decorators": decorators,
                "calls": scope.calls,
                "async_": str(isinstance(fn, pyast.AsyncFunctionDef)).lower()
                if isinstance(fn, pyast.AsyncFunctionDef)
                else None,
            },
            references=scope.hints,
            span=span(start, end),
        )
        self.builder.add("function", qualified, node, start, end, issues=scope.issues)
        for nested_qname, nested in scope.function_objects:
            self._emit_function(nested_qname, nested, scope)

    def _params(self, args: pyast.arguments, scope: Scope) -> list[ast.RoutineParam]:
        params: list[ast.RoutineParam] = []
        positional = list(args.posonlyargs) + list(args.args)
        defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
        for arg, default in zip(positional, defaults, strict=True):
            params.append(
                ast.RoutineParam(
                    name=ast.Identifier(value=arg.arg),
                    default=convert_expr(default, self.ctx) if default else None,
                )
            )
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
            params.append(
                ast.RoutineParam(
                    name=ast.Identifier(value=arg.arg),
                    default=convert_expr(default, self.ctx) if default else None,
                )
            )
        return params

    # -- statements --------------------------------------------------------

    def _process_stmt(self, stmt: pyast.stmt, scope: Scope, *, top_level: bool = False) -> None:
        if isinstance(stmt, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
            qualified = f"{scope.qual_prefix}.{stmt.name}"
            scope.function_objects.append((qualified, stmt))
            return
        if isinstance(stmt, pyast.ClassDef):
            for child in stmt.body:
                self._process_stmt_in_class(child, scope, stmt.name)
            return
        self._walk_calls(stmt, scope)

    def _process_stmt_in_class(self, stmt: pyast.stmt, scope: Scope, class_name: str) -> None:
        if isinstance(stmt, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
            qualified = f"{scope.qual_prefix}.{class_name}.{stmt.name}"
            scope.function_objects.append((qualified, stmt))
            return
        if isinstance(stmt, pyast.ClassDef):
            for child in stmt.body:
                self._process_stmt_in_class(child, scope, f"{class_name}.{stmt.name}")
            return
        self._walk_calls(stmt, scope)

    def _walk_calls(self, node: pyast.AST, scope: Scope) -> None:
        stack = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, (pyast.FunctionDef, pyast.AsyncFunctionDef, pyast.ClassDef)):
                continue
            if isinstance(current, pyast.Call):
                self._handle_call(current, scope)
            if isinstance(current, pyast.AST):
                stack.extend(pyast.iter_child_nodes(current))

    # -- calls ---------------------------------------------------------------

    def _handle_call(self, call: pyast.Call, scope: Scope) -> None:
        dotted = _dotted_path(call.func)
        last = dotted.split(".")[-1] if dotted else ""
        if last in SQL_METHODS or last in READ_SQL_METHODS or last == "to_sql":
            self._handle_sql_call(call, scope, last, dotted or "")
        if self.call_handler is not None:
            self.call_handler(call, scope, dotted or "", self.builder)
        callee = self._resolve_callee(call, scope)
        if callee is not None:
            scope.hints.append(
                self.builder.ref(callee, "calls", span(call.lineno, call.end_lineno or call.lineno))
            )
        scope.calls.append(convert_expr(call, self.ctx))

    def _resolve_callee(self, call: pyast.Call, scope: Scope) -> str | None:
        dotted = _dotted_path(call.func)
        if dotted is None:
            return None
        first, _, rest = dotted.partition(".")
        if rest:
            target = self.imports.get(first)
            if target is None:
                return None
            if self._skip_target(target.split(".")[0]):
                return None
            if dotted == target or dotted.startswith(f"{target}."):
                return dotted
            return f"{target}.{rest}"
        target = scope.local_defs.get(first) or self.imports.get(first)
        if target is None:
            return None
        if self._skip_target(target.split(".")[0]):
            return None
        return target

    def _skip_target(self, top: str) -> bool:
        return top in THIRD_PARTY_TOP or top in sys.stdlib_module_names

    def _handle_sql_call(self, call: pyast.Call, scope: Scope, last: str, dotted: str) -> bool:
        if not call.args:
            return False
        sp = span(call.lineno, call.end_lineno or call.lineno)
        if last == "callproc":
            name = resolve_string(call.args[0], scope.constants)
            if name:
                scope.hints.append(self.builder.ref(name, "calls", sp))
            return True
        if last == "to_sql":
            name = resolve_string(call.args[0], scope.constants)
            if name and _BARE_IDENT.match(name):
                schema = self._kw_string(call, "schema", scope)
                qualified = f"{schema}.{name}" if schema else name
                scope.hints.append(self.builder.ref(qualified, "etl_writes", sp))
            elif name:
                self.builder.embed(scope.owner, name, "to_sql")
            return True
        if last in READ_SQL_METHODS:
            name = resolve_string(call.args[0], scope.constants)
            if name is None:
                scope.issues.append(
                    ast.Issue(
                        "info",
                        f"{self.tool}.dynamic_sql",
                        f"Could not resolve SQL text for {dotted}(...) call.",
                        sp,
                    )
                )
                return True
            if _BARE_IDENT.match(name) and last == "read_sql_table":
                schema = self._kw_string(call, "schema", scope)
                qualified = f"{schema}.{name}" if schema else name
                scope.hints.append(self.builder.ref(qualified, "etl_reads", sp))
            elif _BARE_IDENT.match(name) and last == "read_sql":
                scope.hints.append(self.builder.ref(name, "etl_reads", sp))
            else:
                self.builder.embed(scope.owner, name, dotted.split(".")[-1])
            return True
        # execute / executemany / executescript
        sql = resolve_string(call.args[0], scope.constants)
        if sql is None:
            scope.issues.append(
                ast.Issue(
                    "info",
                    f"{self.tool}.dynamic_sql",
                    f"Could not resolve SQL text for {dotted}(...) call.",
                    sp,
                )
            )
        else:
            self.builder.embed(scope.owner, sql, last)
        return True

    def _kw_string(self, call: pyast.Call, name: str, scope: Scope) -> str | None:
        for keyword in call.keywords:
            if keyword.arg == name:
                return resolve_string(keyword.value, scope.constants)
        return None


def _dotted_path(node: pyast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, pyast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, pyast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _fn_span(fn: pyast.stmt) -> tuple[int, int]:
    start = getattr(fn, "lineno", 1)
    decos = getattr(fn, "decorator_list", [])
    if decos:
        start = min(start, min(d.lineno for d in decos))
    return start, getattr(fn, "end_lineno", None) or start


__all__ = ["ModuleAnalyzer", "Scope", "module_name"]
