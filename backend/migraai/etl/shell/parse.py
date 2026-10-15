"""ETL parser for shell scripts (``.sh`` / ``.bash``).

Objects: ``script`` (qualified by rel_path) and ``function``
(``<rel_path>::<name>``). Invocation extraction (bteq, sqlplus, bcp, nzsql,
nzload, db2, beeline, hive, snowsql, spark-submit, script runners) is shared
with the other ETL tools via ``parse_command_line`` / ``extract_invocations``.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Any

from migraai.discovery import ast
from migraai.etl.python.common import (
    EtlBuilder,
    EtlParseResult,
    decode,
    file_unparsed,
    normalize_path,
    span,
)
from migraai.etl.shell.lexer import tokenize
from migraai.etl.shell.parser import (
    ShellParser,
    ShellScript,
    collect_variables,
    parse_script,
)


@dataclass(slots=True)
class Invocation:
    """One extracted invocation: a reference or embedded SQL."""

    kind: str  # "ref" | "sql"
    ref_kind: str | None = None
    name: str | None = None
    split: bool = True
    text: str | None = None
    dialect_hint: str | None = None
    what: str = "command"
    line: int = 1
    span: ast.Span = field(default_factory=span)


_SPARK_SUBMIT_VALUE_OPTS = {
    "--class",
    "--master",
    "--deploy-mode",
    "--conf",
    "--name",
    "--jars",
    "--py-files",
    "--files",
    "--driver-memory",
    "--executor-memory",
    "--num-executors",
    "--executor-cores",
    "--queue",
    "--packages",
    "--properties-file",
    "--archives",
    "--principal",
    "--keytab",
    "--proxy-user",
    "--driver-cores",
    "--driver-class-path",
    "--total-executor-cores",
}
_RUNNERS = {"sh", "bash", "ksh", "zsh", "python", "python3", "perl", "source", "."}
_SQL_TOOLS = {"bteq", "sqlplus", "nzsql", "db2", "beeline", "hive", "snowsql"}


def _literal(node: ast.Node | None, variables: dict[str, str]) -> str | None:
    if isinstance(node, ast.Literal) and node.type == "string":
        return node.value
    if isinstance(node, ast.Variable):
        return variables.get(node.name)
    if isinstance(node, ast.DialectExpr) and node.name == "shell.param_expansion":
        name_node = node.args.get("name")
        if isinstance(name_node, ast.Identifier):
            if name_node.value in variables:
                return variables[name_node.value]
            default = node.args.get("default")
            if isinstance(default, ast.Literal):
                return default.value
        return None
    if isinstance(node, ast.DialectExpr) and node.name == "shell.word":
        parts: list[str] = []
        for part in node.args.get("parts") or []:
            resolved = _literal(part, variables)
            if resolved is None:
                return None
            parts.append(resolved)
        return "".join(parts)
    if isinstance(node, ast.Identifier):
        return node.value
    return None


def _command_name(
    command: ast.DialectStatement, variables: dict[str, str]
) -> tuple[str | None, str | None]:
    program = command.args.get("program")
    if isinstance(program, ast.Identifier):
        value = program.value
        return posixpath.basename(value), value
    resolved = _literal(program, variables)
    if resolved:
        return posixpath.basename(resolved), resolved
    return None, None


def _arg_strings(command: ast.DialectStatement, variables: dict[str, str]) -> list[str]:
    values: list[str] = []
    for arg in command.args.get("args") or []:
        resolved = _literal(arg, variables)
        values.append(resolved if resolved is not None else "")
    return values


def _redirects(command: ast.DialectStatement) -> list[ast.DialectExpr]:
    return [
        redirect
        for redirect in (command.args.get("redirects") or [])
        if isinstance(redirect, ast.DialectExpr)
    ]


def _heredoc_body(command: ast.DialectStatement) -> str | None:
    for redirect in _redirects(command):
        body = redirect.args.get("heredoc")
        if isinstance(body, ast.Literal):
            return body.value
    return None


def _input_file(command: ast.DialectStatement) -> str | None:
    for redirect in _redirects(command):
        op = redirect.args.get("op", "")
        if isinstance(op, str) and (op == "<" or op.endswith("<")) and "<<" not in op:
            return _literal(redirect.args.get("target"), {})
    return None


def extract_invocations(
    statements: list[ast.Node],
    rel_path: str,
    variables: dict[str, str] | None = None,
    functions: dict[str, str] | None = None,
) -> list[Invocation]:
    """Walk parsed statements and extract refs + embedded SQL per design §7."""
    variables = variables or {}
    functions = functions or {}
    invocations: list[Invocation] = []
    _walk(statements, invocations, rel_path, variables, functions)
    return invocations


def _walk(
    nodes: list[ast.Node] | Any,
    invocations: list[Invocation],
    rel_path: str,
    variables: dict[str, str],
    functions: dict[str, str],
) -> None:
    if isinstance(nodes, ast.DialectStatement):
        if nodes.name == "shell.function":
            return  # nested functions own their refs (handled by the emitter)
        if nodes.name == "shell.command":
            _command_invocations(nodes, invocations, rel_path, variables, functions)
        elif nodes.name == "shell.command_substitution":
            _walk(nodes.args.get("statements"), invocations, rel_path, variables, functions)
            return
        for value in nodes.args.values():
            _walk_value(value, invocations, rel_path, variables, functions)
        return
    if isinstance(nodes, ast.If):
        for cond, body in nodes.branches:
            _walk_value(cond, invocations, rel_path, variables, functions)
            _walk(body, invocations, rel_path, variables, functions)
        _walk(nodes.else_, invocations, rel_path, variables, functions)
        return
    if isinstance(nodes, ast.While):
        _walk_value(nodes.condition, invocations, rel_path, variables, functions)
        _walk(nodes.statements, invocations, rel_path, variables, functions)
        return
    if isinstance(nodes, ast.ForLoop):
        _walk(nodes.statements, invocations, rel_path, variables, functions)
        return
    if isinstance(nodes, list):
        for node in nodes:
            _walk(node, invocations, rel_path, variables, functions)


def _walk_value(
    value: Any,
    invocations: list[Invocation],
    rel_path: str,
    variables: dict[str, str],
    functions: dict[str, str],
) -> None:
    if isinstance(value, ast.DialectStatement):
        _walk(value, invocations, rel_path, variables, functions)
    elif isinstance(value, ast.DialectExpr) and value.name == "shell.command_substitution":
        _walk(value.args.get("statements"), invocations, rel_path, variables, functions)
    elif isinstance(value, ast.Node):
        for field_name in getattr(value, "__dataclass_fields__", {}):
            if field_name == "span":
                continue
            _walk_value(getattr(value, field_name), invocations, rel_path, variables, functions)
    elif isinstance(value, dict):
        for item in value.values():
            _walk_value(item, invocations, rel_path, variables, functions)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_value(item, invocations, rel_path, variables, functions)


def _command_invocations(
    command: ast.DialectStatement,
    invocations: list[Invocation],
    rel_path: str,
    variables: dict[str, str],
    functions: dict[str, str],
) -> None:
    base, full_program = _command_name(command, variables)
    if base is None:
        return
    line = command.span.start_line
    args = _arg_strings(command, variables)
    heredoc = _heredoc_body(command)
    base = base.casefold() if base not in functions else base

    if base in functions:
        invocations.append(
            Invocation(
                kind="ref",
                ref_kind="calls",
                name=functions[base],
                split=False,
                line=line,
                span=command.span,
            )
        )
        return
    if base == "bteq":
        if heredoc is not None:
            invocations.append(_sql(heredoc, "teradata", "bteq", line, command.span))
        input_file = _input_file(command)
        if input_file:
            invocations.append(_path(input_file, rel_path, variables, line, command.span))
        return
    if base == "sqlplus":
        if heredoc is not None:
            invocations.append(_sql(heredoc, "oracle", "sqlplus", line, command.span))
        for arg in args:
            if arg.startswith("@"):
                invocations.append(_path(arg.lstrip("@"), rel_path, variables, line, command.span))
        return
    if base == "bcp":
        _bcp(command, args, invocations, rel_path, variables, line)
        return
    if base == "nzsql":
        _opt_sql_or_file(
            command,
            args,
            {"-c": "sql", "-f": "file"},
            heredoc,
            "netezza",
            invocations,
            rel_path,
            variables,
            line,
        )
        return
    if base == "nzload":
        table = _opt_value(args, "-t")
        datafile = _opt_value(args, "-df")
        if table:
            invocations.append(Invocation("ref", "etl_writes", table, line=line, span=command.span))
        if datafile:
            invocations.append(
                Invocation(
                    "ref",
                    "etl_reads",
                    normalize_path(datafile, rel_path, variables),
                    False,
                    line=line,
                    span=command.span,
                )
            )
        return
    if base == "db2":
        _db2(command, args, heredoc, invocations, rel_path, variables, line)
        return
    if base == "beeline":
        _opt_sql_or_file(
            command,
            args,
            {"-e": "sql", "-f": "file"},
            heredoc,
            "hive",
            invocations,
            rel_path,
            variables,
            line,
        )
        return
    if base == "hive":
        _opt_sql_or_file(
            command,
            args,
            {"-e": "sql", "-f": "file"},
            heredoc,
            "hive",
            invocations,
            rel_path,
            variables,
            line,
        )
        return
    if base == "snowsql":
        _opt_sql_or_file(
            command,
            args,
            {"-q": "sql", "-f": "file"},
            heredoc,
            "snowflake",
            invocations,
            rel_path,
            variables,
            line,
        )
        return
    if base == "spark-submit":
        _spark_submit(command, args, invocations, rel_path, variables, line)
        return
    if base in _RUNNERS:
        for arg in args:
            if arg and not arg.startswith("-"):
                invocations.append(_path(arg, rel_path, variables, line, command.span))
                break
        return
    if full_program and (
        full_program.startswith("./") or full_program.endswith((".sh", ".bash", ".ksh"))
    ):
        invocations.append(_path(full_program, rel_path, variables, line, command.span))


def _sql(text: str, hint: str | None, what: str, line: int, sp: ast.Span) -> Invocation:
    return Invocation(kind="sql", text=text, dialect_hint=hint, what=what, line=line, span=sp)


def _path(
    path: str, rel_path: str, variables: dict[str, str], line: int, sp: ast.Span
) -> Invocation:
    return Invocation(
        kind="ref",
        ref_kind="etl_runs",
        name=normalize_path(path, rel_path, variables),
        split=False,
        line=line,
        span=sp,
    )


def _opt_value(args: list[str], flag: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == flag and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith(flag) and len(arg) > len(flag) and flag.startswith("-"):
            return arg[len(flag) :]
    return None


def _opt_sql_or_file(
    command: ast.DialectStatement,
    args: list[str],
    flags: dict[str, str],
    heredoc: str | None,
    hint: str,
    invocations: list[Invocation],
    rel_path: str,
    variables: dict[str, str],
    line: int,
) -> None:
    sql_flag = next(flag for flag, kind in flags.items() if kind == "sql")
    file_flag = next(flag for flag, kind in flags.items() if kind == "file")
    sql = _opt_value(args, sql_flag)
    if sql is not None:
        invocations.append(_sql(sql, hint, command_name_of(command), line, command.span))
    file_arg = _opt_value(args, file_flag)
    if file_arg:
        invocations.append(_path(file_arg, rel_path, variables, line, command.span))
    if heredoc is not None and sql is None:
        invocations.append(_sql(heredoc, hint, command_name_of(command), line, command.span))


def command_name_of(command: ast.DialectStatement) -> str:
    program = command.args.get("program")
    return program.value if isinstance(program, ast.Identifier) else "sql"


def _bcp(
    command: ast.DialectStatement,
    args: list[str],
    invocations: list[Invocation],
    rel_path: str,
    variables: dict[str, str],
    line: int,
) -> None:
    positional = [a for a in args if not a.startswith("-")]
    if len(positional) < 2:
        return
    table, direction = positional[0], positional[1].casefold()

    def _data_file(index: int, ref_kind: str) -> None:
        if len(positional) > index:
            invocations.append(
                Invocation(
                    "ref",
                    ref_kind,
                    normalize_path(positional[index], rel_path, variables),
                    False,
                    line=line,
                    span=command.span,
                )
            )

    if direction == "in":
        invocations.append(Invocation("ref", "etl_writes", table, line=line, span=command.span))
        _data_file(2, "etl_reads")
    elif direction == "out":
        invocations.append(Invocation("ref", "etl_reads", table, line=line, span=command.span))
        _data_file(2, "etl_writes")
    elif direction == "queryout":
        query_arg = next((a for a in args if a.lower().startswith("select")), None)
        if query_arg:
            invocations.append(_sql(query_arg, "sqlserver", "bcp", line, command.span))
        _data_file(2, "etl_writes")


def _db2(
    command: ast.DialectStatement,
    args: list[str],
    heredoc: str | None,
    invocations: list[Invocation],
    rel_path: str,
    variables: dict[str, str],
    line: int,
) -> None:
    file_arg = _opt_value(args, "-tvf") or _opt_value(args, "-f")
    if file_arg:
        invocations.append(_path(file_arg, rel_path, variables, line, command.span))
        return
    if heredoc is not None:
        invocations.append(_sql(heredoc, "db2", "db2", line, command.span))
        return
    if args and args[0].casefold() == "connect":
        return
    for arg in args:
        if arg and not arg.startswith("-"):
            invocations.append(_sql(arg, "db2", "db2", line, command.span))
            return


def _spark_submit(
    command: ast.DialectStatement,
    args: list[str],
    invocations: list[Invocation],
    rel_path: str,
    variables: dict[str, str],
    line: int,
) -> None:
    index = 0
    class_name = _opt_value(args, "--class")
    if class_name:
        invocations.append(
            Invocation("ref", "etl_runs", class_name, True, line=line, span=command.span)
        )
    while index < len(args):
        arg = args[index]
        if arg in _SPARK_SUBMIT_VALUE_OPTS:
            index += 2
            continue
        if arg.startswith("--"):
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        invocations.append(_path(arg, rel_path, variables, line, command.span))
        break


def apply_invocations(
    builder: EtlBuilder,
    owner: str,
    invocations: list[Invocation],
    refs: list[ast.RefHint],
) -> None:
    for invocation in invocations:
        if invocation.kind == "sql" and invocation.text:
            builder.embed(owner, invocation.text, invocation.what, invocation.dialect_hint)
        elif invocation.kind == "ref" and invocation.name:
            refs.append(
                builder.ref(
                    invocation.name,
                    invocation.ref_kind or "etl_runs",
                    invocation.span or span(invocation.line),
                    split=invocation.split,
                )
            )


# -- reusable command-line API -------------------------------------------------


def parse_command_line(
    text: str,
    rel_path: str,
    line: int = 1,
    variables: dict[str, str] | None = None,
) -> ShellScript:
    """Parse a single command line / snippet (reused by airflow & friends)."""

    tokens, issues = tokenize(text)
    parser = ShellParser(tokens, issues)
    statements = parser.parse()
    merged = {**(variables or {}), **collect_variables(statements)}
    return ShellScript(statements, merged, parser.issues)


# -- file entry point ------------------------------------------------------------


def parse(data: bytes, rel_path: str) -> EtlParseResult:
    try:
        return _parse(data, rel_path)
    except Exception:
        builder = EtlBuilder("shell", rel_path)
        builder.issue("error", "internal", "Unexpected parser failure.")
        result = builder.result()
        result.objects.append(file_unparsed(rel_path, "unexpected parser error"))
        return result


def _parse(data: bytes, rel_path: str) -> EtlParseResult:
    text = decode(data)
    builder = EtlBuilder("shell", rel_path)
    statements, variables, lex_issues = parse_script(text)
    issues = list(lex_issues)

    # index function definitions for calls resolution
    functions: dict[str, str] = {}
    _index_functions(statements, rel_path, functions)

    # build function objects and script refs
    script_refs: list[ast.RefHint] = []
    script_owner = rel_path.replace("\\", "/")
    _emit_objects(statements, builder, rel_path, variables, functions, script_refs, script_owner)

    node = ast.DialectStatement(
        name="shell.script",
        args={
            "path": ast.Identifier(value=rel_path.replace("\\", "/")),
            "statements": statements,
        },
        references=script_refs,
        span=span(1, max((s.span.end_line for s in statements), default=1)),
    )
    builder.add(
        "script",
        rel_path.replace("\\", "/"),
        node,
        1,
        max((s.span.end_line for s in statements), default=1),
        issues=issues,
    )
    return builder.result()


def _index_functions(nodes: list[ast.Node], rel_path: str, functions: dict[str, str]) -> None:
    """Register every function definition (nested included) by bare name."""
    script = ast.DialectStatement(name="shell.script", args={"statements": nodes})
    for node in script.walk():
        if isinstance(node, ast.DialectStatement) and node.name == "shell.function":
            name = node.args.get("name")
            if isinstance(name, ast.Identifier) and name.value:
                functions.setdefault(name.value, f"{rel_path.replace(chr(92), '/')}::{name.value}")


def _emit_objects(
    nodes: list[ast.Node],
    builder: EtlBuilder,
    rel_path: str,
    variables: dict[str, str],
    functions: dict[str, str],
    owner_refs: list[ast.RefHint],
    owner: str,
) -> None:
    """Emit function objects and attach invocation refs/SQL to the owner."""
    for node in nodes:
        if isinstance(node, ast.DialectStatement) and node.name == "shell.function":
            name_node = node.args.get("name")
            fname = name_node.value if isinstance(name_node, ast.Identifier) else ""
            qualified = f"{rel_path.replace(chr(92), '/')}::{fname}"
            body = node.args.get("body") or node.args.get("statements") or []
            fn_refs: list[ast.RefHint] = []
            _emit_objects(body, builder, rel_path, variables, functions, fn_refs, qualified)
            fn_node = ast.DialectStatement(
                name="shell.function",
                args={"name": name_node, "body": body},
                references=fn_refs,
                span=node.span,
            )
            builder.add("function", qualified, fn_node, node.span.start_line, node.span.end_line)
            continue
        invocations = extract_invocations([node], rel_path, variables, functions)
        apply_invocations(builder, owner, invocations, owner_refs)


__all__ = [
    "Invocation",
    "apply_invocations",
    "extract_invocations",
    "parse",
    "parse_command_line",
]
