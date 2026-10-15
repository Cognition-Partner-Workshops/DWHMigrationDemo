"""ETL parser for Airflow DAG definitions.

Python files (delegated from ``python.parse`` when an ``airflow`` import is
present) and dag-factory style ``.yml``/``.yaml`` files both produce ``dag``
objects (qualified by dag_id) and ``task`` objects (``<dag_id>.<task_id>``).

Dependencies (``a >> b``, ``set_upstream``, ``chain``, TaskFlow passing) emit
``runs_after`` references on the *downstream* task pointing at the upstream task.
"""

from __future__ import annotations

import ast as pyast
import posixpath
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import yaml

from migraai.discovery import ast
from migraai.etl.python.common import (
    EtlBuilder,
    attach_recovered,
    EtlParseResult,
    decode,
    file_unparsed,
    normalize_path,
    qname,
    span,
)
from migraai.etl.python.module import module_name
from migraai.etl.python.pyconvert import (
    ConvertContext,
    convert_expr,
    resolve_string,
)
from migraai.etl.python.recover import parse_python_recovering

_DAG_CALLABLES = {"DAG", "dag", "models.DAG", "airflow.DAG"}
_KNOWN_OPERATORS = {
    "BashOperator",
    "PythonOperator",
    "EmptyOperator",
    "DummyOperator",
    "SQLOperator",
    "SnowflakeOperator",
    "PostgresOperator",
    "MsSqlOperator",
    "OracleOperator",
    "HiveOperator",
    "TeradataOperator",
    "VerticaOperator",
    "SQLExecuteQueryOperator",
    "TriggerDagRunOperator",
    "ExternalTaskSensor",
    "BranchPythonOperator",
    "ShortCircuitOperator",
    "EmailOperator",
}
_SQL_KWARGS = {"sql", "hql", "query"}
_DIALECT_BY_CLASS = {
    "snowflake": "snowflake",
    "mssql": "sqlserver",
    "oracle": "oracle",
    "hive": "hive",
    "teradata": "teradata",
    "vertica": "vertica",
    "postgres": None,
    "sqlexecutequery": None,
}


@dataclass(slots=True)
class _Task:
    task_id: str
    qualified: str
    node: pyast.AST | None
    operator: str
    line: int
    end_line: int
    hints: list[ast.RefHint] = field(default_factory=list)
    issues: list[ast.Issue] = field(default_factory=list)
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _Dag:
    dag_id: str
    line: int
    end_line: int
    args: dict[str, Any] = field(default_factory=dict)
    tasks: list[_Task] = field(default_factory=list)
    template_searchpath: str | None = None


def _dotted(node: pyast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, pyast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, pyast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    if isinstance(node, pyast.Call):
        return _dotted(node.func)
    return None


def _const(call: pyast.Call, key: str, constants: dict[str, str]) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == key:
            return resolve_string(keyword.value, constants)
    return None


def _positional(call: pyast.Call, index: int, constants: dict[str, str]) -> str | None:
    if index < len(call.args):
        return resolve_string(call.args[index], constants)
    return None


def _dag_id_of(call: pyast.Call, constants: dict[str, str]) -> str | None:
    return _const(call, "dag_id", constants) or _positional(call, 0, constants)


def _template_searchpath(call: pyast.Call, constants: dict[str, str]) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "template_searchpath" and isinstance(
            keyword.value, (pyast.List, pyast.Tuple)
        ):
            for item in keyword.value.elts:
                value = resolve_string(item, constants)
                if value:
                    return value
    return None


class _AirflowPy:
    def __init__(self, rel_path: str, builder: EtlBuilder) -> None:
        self.rel_path = rel_path
        self.builder = builder
        self.constants: dict[str, str] = {}
        self.ctx = ConvertContext(tool="airflow")
        self.dags: dict[str, _Dag] = {}
        self.dag_vars: dict[str, str] = {}  # variable name -> dag_id
        self.dag_funcs: dict[str, str] = {}  # decorated function name -> dag_id
        self.tasks: dict[str, _Task] = {}  # local name (var or task_id) -> task
        self.deps: list[tuple[str, str, int]] = []  # (upstream local, downstream local, line)
        self.taskflow: dict[str, str] = {}  # variable -> callee key (task name)

    # -- dag + task discovery -------------------------------------------------

    def analyze(self, tree: pyast.Module) -> None:
        for stmt in tree.body:
            if isinstance(stmt, pyast.Assign):
                value = resolve_string(stmt.value, self.constants)
                if value is not None:
                    for target in stmt.targets:
                        if isinstance(target, pyast.Name):
                            self.constants[target.id] = value
            self._visit_top(stmt)
        self._emit()

    def _visit_top(self, stmt: pyast.stmt) -> None:
        # `with DAG(...) as d:` blocks
        if isinstance(stmt, (pyast.With, pyast.AsyncWith)):
            for item in stmt.items:
                call = item.context_expr
                if isinstance(call, pyast.Call) and self._is_dag_call(call):
                    self._register_dag(call, stmt, item)
                    for inner in stmt.body:
                        self._visit_dag_body(inner, self._dag_of(call), "")
                    return
            for inner in stmt.body:
                self._visit_top(inner)
            return
        if isinstance(stmt, pyast.Assign):
            call = stmt.value
            if isinstance(call, pyast.Call) and self._is_dag_call(call):
                self._register_dag(call, stmt, None)
                for target in stmt.targets:
                    if isinstance(target, pyast.Name):
                        self.dag_vars[target.id] = self._dag_of(call) or ""
                return
        if isinstance(stmt, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
            for decorator in stmt.decorator_list:
                target = decorator if isinstance(decorator, pyast.Call) else decorator
                name = _dotted(target)
                if name and name.split(".")[-1] == "dag":
                    dag_id = (
                        _const(decorator, "dag_id", self.constants)
                        if isinstance(decorator, pyast.Call)
                        else None
                    ) or stmt.name
                    dag = _Dag(
                        dag_id=dag_id, line=stmt.lineno, end_line=stmt.end_lineno or stmt.lineno
                    )
                    self.dags[dag_id] = dag
                    self.dag_funcs[stmt.name] = dag_id
                    for inner in stmt.body:
                        self._visit_dag_body(inner, dag_id, "")
                    return
            return
        # module-level task bound via dag=<dag var> kwarg
        if isinstance(stmt, pyast.Assign) and isinstance(stmt.value, pyast.Call):
            call = stmt.value
            for keyword in call.keywords:
                if keyword.arg == "dag" and isinstance(keyword.value, pyast.Name):
                    dag_id = self.dag_vars.get(keyword.value.id)
                    if dag_id and dag_id in self.dags:
                        task = self._task_from_call(call, dag_id, "", stmt)
                        if task is not None:
                            for target in stmt.targets:
                                if isinstance(target, pyast.Name):
                                    self.tasks[target.id] = task
                            self.dags[dag_id].tasks.append(task)
                            return
        # module-level task deps like t1 >> t2 outside a with block
        self._visit_deps(stmt)

    def _is_dag_call(self, call: pyast.Call) -> bool:
        dotted = _dotted(call.func)
        if dotted is None:
            return False
        return dotted.split(".")[-1] == "DAG" or dotted in _DAG_CALLABLES

    def _register_dag(
        self, call: pyast.Call, stmt: pyast.stmt, item: pyast.withitem | None
    ) -> None:
        dag_id = self._dag_of(call)
        if dag_id is None:
            self.builder.issue(
                "warning", "dag_id_unresolved", "Could not resolve dag_id.", span(stmt.lineno)
            )
            return
        dag = _Dag(
            dag_id=dag_id,
            line=stmt.lineno,
            end_line=stmt.end_lineno or stmt.lineno,
            template_searchpath=_template_searchpath(call, self.constants),
        )
        self.dags[dag_id] = dag
        if item is not None and isinstance(item.optional_vars, pyast.Name):
            self.dag_vars[item.optional_vars.id] = dag_id

    def _dag_of(self, call: pyast.Call) -> str | None:
        return _dag_id_of(call, self.constants)

    def _visit_dag_body(self, stmt: pyast.stmt, dag_id: str | None, prefix: str) -> None:
        if dag_id is None:
            return
        if isinstance(stmt, (pyast.With, pyast.AsyncWith)):
            for item in stmt.items:
                call = item.context_expr
                dotted = _dotted(call.func) if isinstance(call, pyast.Call) else None
                if dotted and dotted.split(".")[-1] == "TaskGroup":
                    group = _positional(call, 0, self.constants) or _const(
                        call, "group_id", self.constants
                    )
                    for inner in stmt.body:
                        self._visit_dag_body(inner, dag_id, prefix + (group or "") + ".")
                    return
            for inner in stmt.body:
                self._visit_dag_body(inner, dag_id, prefix)
            return
        if isinstance(stmt, pyast.Assign):
            call = stmt.value
            if isinstance(call, pyast.Call):
                task = self._task_from_call(call, dag_id, prefix, stmt)
                if task is not None:
                    for target in stmt.targets:
                        if isinstance(target, pyast.Name):
                            self.tasks[target.id] = task
                    self.dags[dag_id].tasks.append(task)
                    return
                # TaskFlow: x = extract() records x -> 'extract' for later deps
                callee = _dotted(call.func)
                if callee:
                    key = callee.split(".")[-1]
                    for target in stmt.targets:
                        if isinstance(target, pyast.Name):
                            self.taskflow[target.id] = key
                    self._taskflow_args(call, dag_id)
            return
        if isinstance(stmt, pyast.Expr):
            self._handle_expr(stmt.value, dag_id, prefix)
            return
        if isinstance(stmt, pyast.If):
            for inner in stmt.body + stmt.orelse:
                self._visit_dag_body(inner, dag_id, prefix)
            return
        if isinstance(stmt, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
            for decorator in stmt.decorator_list:
                name = _dotted(decorator.func if isinstance(decorator, pyast.Call) else decorator)
                if name and name.split(".")[-1].startswith("task"):
                    task_id = (
                        _const(decorator, "task_id", self.constants)
                        if isinstance(decorator, pyast.Call)
                        else None
                    ) or stmt.name
                    task = self._make_task(
                        dag_id, prefix + task_id, stmt, f"@task:{name}", stmt.lineno
                    )
                    self.tasks[stmt.name] = task
                    self.dags[dag_id].tasks.append(task)
                    for inner in stmt.body:
                        self._visit_dag_body(inner, dag_id, prefix)
                    return
            for inner in stmt.body:
                self._visit_dag_body(inner, dag_id, prefix)
            return
        self._visit_deps(stmt)

    def _handle_expr(self, value: pyast.expr, dag_id: str, prefix: str) -> None:
        if isinstance(value, pyast.Call):
            task = self._task_from_call(value, dag_id, prefix, None)
            if task is not None:
                self.tasks[task.task_id] = task
                self.dags[dag_id].tasks.append(task)
                return
            # a call whose args reference taskflow vars creates a dep
            self._taskflow_args(value, dag_id)
        # deps inside the expression (a >> b, chains)
        self._visit_deps_in(value)

    def _taskflow_args(self, call: pyast.Call, dag_id: str) -> None:
        callee = _dotted(call.func)
        if not callee:
            return
        down_key = callee.split(".")[-1]
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            for name in self._dep_names(arg):
                upstream_key = self.taskflow.get(name)
                if upstream_key:
                    self.deps.append((upstream_key, down_key, call.lineno))
        if call.args or call.keywords:
            for arg in list(call.args) + [kw.value for kw in call.keywords]:
                self._visit_deps_in(arg)

    def _task_from_call(
        self, call: pyast.Call, dag_id: str, prefix: str, stmt: pyast.stmt | None
    ) -> _Task | None:
        dotted = _dotted(call.func)
        if dotted is None:
            return None
        operator = dotted.split(".")[-1]
        stmt_line = stmt.lineno if stmt is not None else call.lineno
        task_id = _const(call, "task_id", self.constants)
        is_task = operator in _KNOWN_OPERATORS or operator.endswith(("Operator", "Sensor"))
        if is_task and operator not in _KNOWN_OPERATORS:
            self.builder.issue(
                "info",
                "unknown_operator",
                f"Unknown operator {operator!r}; treating as a task.",
                span(stmt_line),
            )
        if not is_task and task_id is not None:
            is_task = True
        if not is_task:
            return None
        if task_id is None:
            if (
                isinstance(stmt, pyast.Assign)
                and stmt.targets
                and isinstance(stmt.targets[0], pyast.Name)
            ):
                task_id = stmt.targets[0].id
            else:
                self.builder.issue(
                    "warning",
                    "missing_task_id",
                    f"Operator {operator!r} without task_id.",
                    span(stmt_line),
                )
                return None
        task = self._make_task(dag_id, prefix + task_id, stmt or call, operator, stmt_line)
        self.tasks[prefix + task_id] = task
        self._task_sql_and_shell(task, call, dag_id)
        return task

    def _make_task(
        self, dag_id: str, task_id: str, stmt: pyast.stmt, operator: str, line: int
    ) -> _Task:
        qualified = f"{dag_id}.{task_id}"
        return _Task(
            task_id=task_id,
            qualified=qualified,
            node=stmt,
            operator=operator,
            line=line,
            end_line=stmt.end_lineno or line,
        )

    def _task_sql_and_shell(self, task: _Task, call: pyast.Call, dag_id: str) -> None:
        from migraai.etl.shell.parse import extract_invocations, parse_command_line

        hint = None
        lowered = task.operator.casefold()
        for prefix, dialect in _DIALECT_BY_CLASS.items():
            if lowered.startswith(prefix):
                hint = dialect
                break
        for keyword in call.keywords:
            key = keyword.arg
            if key in _SQL_KWARGS:
                if isinstance(keyword.value, (pyast.List, pyast.Tuple)):
                    values = [
                        item
                        for item in (
                            resolve_string(item, self.constants) for item in keyword.value.elts
                        )
                        if item
                    ]
                else:
                    value = resolve_string(keyword.value, self.constants)
                    if value is None:
                        continue
                    values = [value]
                for item in values:
                    if item.lower().endswith((".sql", ".hql")):
                        task.hints.append(
                            self.builder.ref(
                                self._sql_path(item, dag_id),
                                "etl_runs",
                                span(task.line),
                                split=False,
                            )
                        )
                    else:
                        self.builder.embed(task.qualified, item, key, hint)
            elif key == "bash_command":
                value = resolve_string(keyword.value, self.constants)
                if value is None:
                    continue
                script = parse_command_line(value, self.rel_path, task.line)
                invocations = extract_invocations(
                    script.statements, self.rel_path, script.variables
                )
                for invocation in invocations:
                    if invocation.kind == "sql" and invocation.text:
                        self.builder.embed(
                            task.qualified, invocation.text, "bash_command", invocation.dialect_hint
                        )
                    elif invocation.kind == "ref" and invocation.name:
                        task.hints.append(
                            self.builder.ref(
                                invocation.name,
                                invocation.ref_kind or "etl_runs",
                                span(task.line),
                                split=invocation.split,
                            )
                        )
        # operator-specific refs
        lowered = task.operator.casefold()
        if lowered == "triggerdagrunoperator":
            target = _const(call, "trigger_dag_id", self.constants)
            if target:
                task.hints.append(self.builder.ref(target, "etl_runs", span(task.line)))
        elif lowered == "externaltasksensor":
            dag = _const(call, "external_dag_id", self.constants) or _positional(
                call, 0, self.constants
            )
            ext_task = _const(call, "external_task_id", self.constants) or _positional(
                call, 1, self.constants
            )
            if dag:
                name = f"{dag}.{ext_task}" if ext_task else dag
                task.hints.append(self.builder.ref(name, "etl_reads", span(task.line)))

    def _sql_path(self, item: str, dag_id: str) -> str:
        dag = self.dags.get(dag_id)
        if dag and dag.template_searchpath:
            # design: template_searchpath entries are pack-root relative
            return posixpath.normpath(f"{dag.template_searchpath}/{item}")
        return normalize_path(item, self.rel_path)

    # -- dependencies -----------------------------------------------------------

    def _visit_deps(self, stmt: pyast.stmt) -> None:
        for node in pyast.walk(stmt):
            if isinstance(node, pyast.BinOp) and isinstance(node.op, (pyast.RShift, pyast.LShift)):
                lefts = self._dep_names(node.left)
                rights = self._dep_names(node.right)
                if isinstance(node.op, pyast.RShift):
                    for left in lefts:
                        for right in rights:
                            self.deps.append((left, right, stmt.lineno))
                else:
                    for left in lefts:
                        for right in rights:
                            self.deps.append((right, left, stmt.lineno))
            elif isinstance(node, pyast.Call):
                dotted = _dotted(node.func)
                if not dotted:
                    continue
                last = dotted.split(".")[-1]
                if last in {"set_upstream", "set_downstream"} and node.args:
                    base = dotted.rpartition(".")[0].split(".")[-1]
                    others = self._dep_names(node.args[0])
                    for other in others:
                        if last == "set_upstream":
                            self.deps.append((other, base, stmt.lineno))
                        else:
                            self.deps.append((base, other, stmt.lineno))
                elif last in {"chain", "cross_downstream"}:
                    self._handle_chain(node, stmt)

    def _dep_names(self, node: pyast.AST) -> list[str]:
        if isinstance(node, pyast.Name):
            return [node.id]
        if isinstance(node, (pyast.List, pyast.Tuple)):
            out: list[str] = []
            for item in node.elts:
                out.extend(self._dep_names(item))
            return out
        if isinstance(node, pyast.Call):
            task = self._task_from_inline(node)
            if task is not None:
                return [task]
            key = None
            dotted = _dotted(node.func)
            if dotted:
                key = dotted.split(".")[-1]
            return [key] if key else []
        if isinstance(node, pyast.BinOp) and isinstance(node.op, (pyast.RShift, pyast.LShift)):
            return self._dep_names(node.right)
        return []

    def _visit_deps_in(self, node: pyast.AST) -> None:
        wrapper = pyast.Expr(
            value=node,
            lineno=getattr(node, "lineno", 1),
            col_offset=0,
            end_lineno=getattr(node, "end_lineno", 1),
            end_col_offset=1,
        )
        self._visit_deps(wrapper)

    def _task_from_inline(self, call: pyast.Call) -> str | None:
        dotted = _dotted(call.func)
        if not dotted:
            return None
        task_id = _const(call, "task_id", self.constants)
        return task_id

    def _handle_chain(self, call: pyast.Call, stmt: pyast.stmt) -> None:
        sequence: list[str] = []
        for arg in call.args:
            names = self._dep_names(arg)
            if names:
                sequence.append(names[0] if len(names) == 1 else names[-1])
                for prev, nxt in zip(names, names[1:], strict=False):
                    self.deps.append((prev, nxt, stmt.lineno))
        for prev, nxt in zip(sequence, sequence[1:], strict=False):
            self.deps.append((prev, nxt, stmt.lineno))
        # TaskFlow: f1() >> f2() handled via Name ops; also `x = a(); b(x)`
        # handled in _emit via argument passing.

    # -- emit --------------------------------------------------------------------

    def _emit(self) -> None:
        # resolve dep names to qualified task names
        for upstream, downstream, line in self.deps:
            up_task = self.tasks.get(upstream)
            down_task = self.tasks.get(downstream)
            if up_task is None or down_task is None:
                continue
            down_task.hints.append(self.builder.ref(up_task.qualified, "runs_after", span(line)))
        for dag in self.dags.values():
            for task in dag.tasks:
                node = ast.DialectStatement(
                    name="airflow.task",
                    args={
                        "task_id": ast.Identifier(value=task.task_id),
                        "operator": ast.Identifier(value=task.operator),
                        "params": self._task_params(task),
                    },
                    references=task.hints,
                    span=span(task.line, task.end_line),
                )
                issues = task.issues
                self.builder.add("task", task.qualified, node, task.line, task.end_line, issues)
            dag_node = ast.DialectStatement(
                name="airflow.dag",
                args={
                    "dag_id": ast.Identifier(value=dag.dag_id),
                    "tasks": [ast.Identifier(value=task.task_id) for task in dag.tasks],
                },
                references=[],
                span=span(dag.line, dag.end_line),
            )
            self.builder.add("dag", dag.dag_id, dag_node, dag.line, dag.end_line)

    def _task_params(self, task: _Task) -> list[ast.Node]:
        params: list[ast.Node] = []
        node = task.node
        call = node.value if isinstance(node, pyast.Assign) else None
        if isinstance(node, pyast.Expr):
            call = node.value
        if isinstance(call, pyast.Call):
            for keyword in call.keywords:
                if keyword.arg in _SQL_KWARGS | {"bash_command"} or keyword.arg == "task_id":
                    continue
                params.append(
                    ast.NamedArg(
                        name=ast.Identifier(value=keyword.arg),
                        value=convert_expr(keyword.value, self.ctx),
                    )
                )
        return params


def _parse_python(data: bytes, rel_path: str, builder: EtlBuilder) -> None:
    text = decode(data)
    recovered = parse_python_recovering(text, "airflow")
    if recovered.tree is None:
        builder.objects.append(file_unparsed(rel_path, "unrecoverable syntax errors"))
        builder.issues.extend(recovered.issues)
        return
    analyzer = _AirflowPy(rel_path, builder)
    analyzer.analyze(recovered.tree)
    builder.issues.extend(recovered.issues + analyzer.ctx.issues)
    leftover = attach_recovered(builder.objects, recovered.recovered, recovered.issues)
    if leftover:
        qualified = module_name(rel_path)
        end = max(blk.span.end_line for blk in leftover)
        node = ast.DialectStatement(
            name="airflow.module",
            args={
                "name": qname(qualified),
                "recovered": list(leftover),
            },
            references=[],
            span=span(1, end),
        )
        builder.add("module", qualified, node, 1, end, issues=list(recovered.issues))


# ---------------------------------------------------------------------------
# dag-factory YAML
# ---------------------------------------------------------------------------


def _parse_yaml(text: str, rel_path: str, builder: EtlBuilder) -> None:
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as error:
        builder.issue("error", "syntax", f"Malformed YAML: {error}")
        builder.objects.append(file_unparsed(rel_path, "malformed YAML"))
        return
    if not isinstance(payload, dict) or not any(
        isinstance(value, dict) and "tasks" in value for value in payload.values()
    ):
        builder.issue(
            "warning",
            "unrecognized_yaml",
            "YAML file is not a dag-factory DAG definition.",
        )
        return
    lines = text.split("\n")

    def _key_line(key: str, start: int) -> int:
        for index in range(start, len(lines)):
            if lines[index].lstrip().startswith(f"{key}:"):
                return index + 1
        return start + 1

    search_from = 0
    for dag_id, spec in payload.items():
        if not isinstance(spec, dict) or "tasks" not in spec:
            continue
        dag_line = _key_line(str(dag_id), search_from)
        search_from = dag_line - 1
        dag = _Dag(dag_id=str(dag_id), line=dag_line, end_line=dag_line)
        tasks_spec = spec.get("tasks") or {}
        local: dict[str, _Task] = {}
        for task_id, task_spec in tasks_spec.items():
            if not isinstance(task_spec, dict):
                task_spec = {}
            operator = str(task_spec.get("operator", "EmptyOperator"))
            task_line = _key_line(str(task_id), search_from)
            search_from = task_line - 1
            task = _Task(
                task_id=str(task_id),
                qualified=f"{dag_id}.{task_id}",
                node=None,
                operator=operator,
                line=task_line,
                end_line=task_line,
                args={},
            )
            if operator not in _KNOWN_OPERATORS and not operator.endswith(("Operator", "Sensor")):
                builder.issue(
                    "info",
                    "unknown_operator",
                    f"Unknown operator {operator!r}; treating as a task.",
                    span(task_line),
                )
            _yaml_task_extras(task, task_spec, builder)
            dag.tasks.append(task)
            local[str(task_id)] = task
        for task_id, task_spec in tasks_spec.items():
            if not isinstance(task_spec, dict):
                continue
            for dep in task_spec.get("dependencies") or []:
                upstream = local.get(str(dep))
                downstream = local.get(str(task_id))
                if upstream is not None and downstream is not None:
                    downstream.hints.append(
                        builder.ref(upstream.qualified, "runs_after", span(downstream.line))
                    )
        for task in dag.tasks:
            node = ast.DialectStatement(
                name="airflow.task",
                args={
                    "task_id": ast.Identifier(value=task.task_id),
                    "operator": ast.Identifier(value=task.operator),
                },
                references=task.hints,
                span=span(task.line),
            )
            builder.add("task", task.qualified, node, task.line, task.line)
        dag_node = ast.DialectStatement(
            name="airflow.dag",
            args={
                "dag_id": ast.Identifier(value=dag.dag_id),
                "schedule_interval": str(spec.get("schedule_interval", "")) or None,
                "tasks": [ast.Identifier(value=task.task_id) for task in dag.tasks],
            },
            references=[],
            span=span(dag.line),
        )
        builder.add("dag", dag.dag_id, dag_node, dag.line, dag.line)


def _yaml_task_extras(task: _Task, spec: dict[str, Any], builder: EtlBuilder) -> None:
    for key in _SQL_KWARGS:
        value = spec.get(key)
        if not isinstance(value, (str, list)):
            continue
        items = value if isinstance(value, list) else [value]
        hint = None
        for prefix, dialect in _DIALECT_BY_CLASS.items():
            if task.operator.casefold().startswith(prefix):
                hint = dialect
                break
        for item in items:
            if not isinstance(item, str):
                continue
            if item.lower().endswith((".sql", ".hql")):
                task.hints.append(builder.ref(item, "etl_runs", span(task.line), split=False))
            else:
                builder.embed(task.qualified, item, key, hint)
    bash = spec.get("bash_command")
    if isinstance(bash, str):
        from migraai.etl.shell.parse import extract_invocations, parse_command_line

        script = parse_command_line(bash, builder.rel_path, 1)
        for invocation in extract_invocations(
            script.statements, builder.rel_path, script.variables
        ):
            if invocation.kind == "sql" and invocation.text:
                builder.embed(
                    task.qualified, invocation.text, "bash_command", invocation.dialect_hint
                )
            elif invocation.kind == "ref" and invocation.name:
                task.hints.append(
                    builder.ref(
                        invocation.name,
                        invocation.ref_kind or "etl_runs",
                        span(task.line),
                        split=invocation.split,
                    )
                )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def parse(data: bytes, rel_path: str) -> EtlParseResult:
    try:
        return _parse(data, rel_path)
    except Exception:
        builder = EtlBuilder("airflow", rel_path)
        builder.issue("error", "internal", "Unexpected parser failure.")
        result = builder.result()
        result.objects.append(file_unparsed(rel_path, "unexpected parser error"))
        return result


def _parse(data: bytes, rel_path: str) -> EtlParseResult:
    builder = EtlBuilder("airflow", rel_path)
    suffix = PurePosixPath(rel_path.replace("\\", "/")).suffix.casefold()
    if suffix in {".yml", ".yaml"}:
        _parse_yaml(decode(data), rel_path, builder)
        return builder.result()
    _parse_python(data, rel_path, builder)
    return builder.result()


__all__ = ["parse"]
