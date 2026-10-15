"""Informatica PowerCenter XML parser + the .xml router (design §4, §13)."""

from __future__ import annotations

from migraai.discovery import ast
from migraai.etl.informatica.expr import parse as parse_expr
from migraai.etl.python.common import (
    EtlBuilder,
    EtlParseResult,
    XmlNode,
    XmlParseError,
    file_unparsed,
    parse_xml,
    redact,
    span,
)

_SQL_ATTRIBUTES = {"Sql Query", "Pre SQL", "Post SQL", "Lookup Sql Override", "Update Override"}
_DIALECT_BY_DB = {
    "oracle": "oracle",
    "teradata": "teradata",
    "microsoft sql server": "sqlserver",
    "db2": "db2",
    "netezza": "netezza",
}
_KNOWN_FOLDER_CHILDREN = {
    "WORKFLOW",
    "WORKLET",
    "SESSION",
    "MAPPING",
    "MAPPLET",
    "TRANSFORMATION",
    "SHORTCUT",
    "SCHEDULER",
    "SOURCE",
    "TARGET",
    "CONFIG",
    "TASK",
    "FOLDER",
    "TARGETLOADORDERGROUP",
    "ERPINFO",
    "SESSIONEXTENSION",
    "CONFIGREFERENCE",
    "CUSTOMDIAG",
    "METADATAEXTENSION",
    "TABLEATTRIBUTE",
    "ATTRIBUTE",
    "SCHEDULERATTRIBUTE",
}


def parse(data: bytes, rel_path: str) -> EtlParseResult:
    builder = EtlBuilder("informatica", rel_path)
    try:
        root = parse_xml(data)
    except XmlParseError as error:
        builder.issue("error", "malformed_xml", str(error), span(error.line))
        builder.objects.append(file_unparsed(rel_path, "malformed or forbidden XML", error.line))
        return builder.result()
    local = root.local.upper()
    if local in {"POWERMART", "REPOSITORY"}:
        _parse_informatica(root, builder)
    elif local == "DSEXPORT":
        from migraai.etl.datastage.parse import parse_dsexport

        parse_dsexport(root, builder, rel_path, tool_objects=True)
        for issue in builder.issues:
            if issue.code.startswith("informatica."):
                issue.code = issue.code.replace("informatica.", "datastage.", 1)
        result = builder.result()
        result.objects = builder.objects
        return result
    elif local == "DEFTABLE" or (local in {"FOLDER", "SMART_FOLDER"} and root.find_all("JOB")):
        from migraai.etl.controlm.parse import parse_deftable

        parse_deftable(root, builder, rel_path)
        result = builder.result()
        for issue in result.issues:
            if issue.code.startswith("informatica."):
                issue.code = issue.code.replace("informatica.", "controlm.", 1)
        return result
    elif root.tag.endswith("DTS:Executable") or local == "EXECUTABLE":
        from migraai.etl.ssis.parse import parse_ssis_root

        parse_ssis_root(root, builder, rel_path)
        result = builder.result()
        for issue in result.issues:
            if issue.code.startswith("informatica."):
                issue.code = issue.code.replace("informatica.", "ssis.", 1)
        return result
    else:
        builder.issue("warning", "unrecognized_xml", "Unrecognized XML root element.")
        builder.objects.append(file_unparsed(rel_path, "unrecognized XML", root.line))
        return builder.result()
    return builder.result()


def _parse_informatica(root: XmlNode, builder: EtlBuilder) -> None:
    folders = [n for n in _walk(root) if n.local == "FOLDER"]
    for folder in folders:
        folder_name = folder.get("NAME", "FOLDER") or "FOLDER"
        sources: dict[str, tuple[str | None, str]] = {}
        for src in folder.find_all("SOURCE"):
            key = src.get("NAME") or ""
            sources[key] = (src.get("DATABASETYPE"), src.get("DBDNAME"))
        shortcut_names = {sc.get("NAME") for sc in folder.find_all("SHORTCUT") if sc.get("NAME")}
        folder_hint = next(
            (_dbtype_hint(dbtype) for dbtype, _dbdname in sources.values() if _dbtype_hint(dbtype)),
            None,
        )
        for child in folder.children:
            local = child.local.upper()
            name = child.get("NAME", "?") or "?"
            if local in {"WORKFLOW", "WORKLET"}:
                _emit_workflow(child, folder_name, builder)
            elif local == "SESSION":
                _emit_session(child, folder_name, builder, folder_hint)
            elif local in {"MAPPING", "MAPPLET"}:
                _emit_mapping(child, folder_name, builder, sources, shortcut_names)
            elif local == "TRANSFORMATION":
                if child.get("REUSABLE", "").upper() == "YES":
                    _emit_transformation(child, folder_name, f"{folder_name}.{name}", builder)
            elif local == "SHORTCUT":
                _emit_shortcut(child, folder_name, builder)
            elif local == "SCHEDULER":
                _emit_scheduler(child, folder_name, builder)
            elif local not in _KNOWN_FOLDER_CHILDREN:
                builder.issue(
                    "info",
                    "unsupported_element",
                    f"Unsupported FOLDER element {local}.",
                    span(child.line),
                )


_DBTYPE_HINTS = {
    "oracle": "oracle",
    "teradata": "teradata",
    "microsoft sql server": "sqlserver",
    "db2": "db2",
    "netezza": "netezza",
}


def _dbtype_hint(dbtype: str | None) -> str | None:
    from migraai.discovery.registry import get_sql_parser

    if not dbtype:
        return None
    hint = _DBTYPE_HINTS.get(dbtype.strip().casefold())
    return hint if hint and get_sql_parser(hint) is not None else None


def _walk(node: XmlNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _emit_workflow(node: XmlNode, folder: str, builder: EtlBuilder) -> None:
    name = node.get("NAME", "?") or "?"
    qualified = f"{folder}.{name}"
    hints: list[ast.RefHint] = []
    issues: list[ast.Issue] = []
    args: dict = {"name": ast.Identifier(value=name)}
    links = []
    for task in node.find_all("TASKINSTANCE") + node.find_all("TASK"):
        task_name = task.get("NAME", "?") or "?"
        task_type = (task.get("TASKTYPE") or task.get("TYPE") or "").casefold()
        if task_type in {"session", "worklet"}:
            hints.append(builder.ref(f"{folder}.{task_name}", "etl_runs", span(task.line)))
        elif task_type == "command":
            for vp in _walk(task):
                if vp.local == "VALUEPAIR" and (vp.get("NAME") or "").casefold() in {
                    "command",
                    "commandtext",
                }:
                    _shell_command(vp.get("VALUE") or "", qualified, task.line, hints, builder)
    for child in node.find_all("SESSION"):
        if child.get("REUSABLE", "").upper() != "YES":
            _emit_session(child, folder, builder)
    for link in node.find_all("WORKFLOWLINK"):
        condition = link.get("CONDITION", "")
        expr = None
        if condition:
            expr, _expr_issues, _calls = parse_expr(condition, link.line)
            issues.extend(_expr_issues)
            links.append(
                ast.DialectExpr(
                    name="informatica.link",
                    args={
                        "from": ast.Identifier(value=link.get("FROMTASK", "")),
                        "to": ast.Identifier(value=link.get("TOTASK", "")),
                        "condition": expr,
                    },
                    span=span(link.line),
                )
            )
        else:
            links.append(
                ast.DialectExpr(
                    name="informatica.link",
                    args={
                        "from": ast.Identifier(value=link.get("FROMTASK", "")),
                        "to": ast.Identifier(value=link.get("TOTASK", "")),
                    },
                    span=span(link.line),
                )
            )
    if links:
        args["links"] = links
    sched = node.find("SCHEDULER")
    if sched is not None and sched.get("REUSABLE", "").upper() == "YES":
        hints.append(
            builder.ref(f"{folder}.{sched.get('NAME', '')}", "schedules", span(sched.line))
        )
    sname = node.get("SCHEDULERNAME")
    if sname:
        hints.append(builder.ref(f"{folder}.{sname}", "schedules", span(node.line)))
    stmt = ast.DialectStatement(
        name="informatica.workflow",
        args=args,
        references=hints,
        span=span(node.line, node.end_line),
    )
    builder.add(
        "worklet" if node.local == "WORKLET" else "workflow",
        qualified,
        stmt,
        node.line,
        node.end_line,
        issues,
    )


def _emit_session(node: XmlNode, folder: str, builder: EtlBuilder, hint: str | None = None) -> None:
    name = node.get("NAME", "?") or "?"
    qualified = f"{folder}.{name}"
    hints: list[ast.RefHint] = []
    mapping_name = node.get("MAPPINGNAME")
    if mapping_name:
        hints.append(builder.ref(f"{folder}.{mapping_name}", "etl_runs", span(node.line)))
    for el in _walk(node):
        if el.local == "ATTRIBUTE":
            attr = (el.get("NAME") or "").strip()
            value = el.get("VALUE") or ""
            if attr in _SQL_ATTRIBUTES and value.strip():
                builder.embed(qualified, value, attr, hint)
            elif (
                attr.casefold()
                in {
                    "pre-session command",
                    "post-session success command",
                    "post-session failure command",
                }
                and value.strip()
            ):
                _shell_command(value, qualified, el.line, hints, builder)
        elif el.local == "SESSTRANSFORMATIONINST":
            for at in el.find_all("ATTRIBUTE"):
                if (at.get("NAME") or "") in _SQL_ATTRIBUTES and (at.get("VALUE") or "").strip():
                    builder.embed(qualified, at.get("VALUE") or "", at.get("NAME") or "sql", hint)
    stmt = ast.DialectStatement(
        name="informatica.session",
        args={"name": ast.Identifier(value=name)},
        references=hints,
        span=span(node.line, node.end_line),
    )
    builder.add("session", qualified, stmt, node.line, node.end_line)


def _emit_mapping(
    node: XmlNode,
    folder: str,
    builder: EtlBuilder,
    sources: dict[str, tuple[str | None, str]],
    shortcut_names: set[str] | None = None,
) -> None:
    name = node.get("NAME", "?") or "?"
    qualified = f"{folder}.{name}"
    hints: list[ast.RefHint] = []
    args: dict = {"name": ast.Identifier(value=name)}
    connectors = []
    variables = [v.get("NAME") for v in node.find_all("MAPPINGVARIABLE") if v.get("NAME")]
    mapping_hint = next(
        (
            hint
            for inst in node.find_all("INSTANCE")
            if (inst.get("TYPE") or inst.get("TRANSFORMATION_TYPE") or "").upper() == "SOURCE"
            for dbtype in (
                (
                    inst.get("DATABASETYPE")
                    or (sources.get(inst.get("TRANSFORMATION_NAME") or "") or (None,))[0]
                ),
            )
            if (hint := _dbtype_hint(dbtype))
        ),
        None,
    )
    for inst in node.find_all("INSTANCE"):
        inst_type = (inst.get("TYPE") or inst.get("TRANSFORMATION_TYPE") or "").upper()
        inst_name = inst.get("NAME") or inst.get("TRANSFORMATION_NAME") or "?"
        tname = inst.get("TRANSFORMATION_NAME") or inst_name
        if inst_type == "SOURCE":
            if shortcut_names and tname in shortcut_names:
                hints.append(builder.ref(f"{folder}.{tname}", "etl_reads", span(inst.line)))
            else:
                src = sources.get(tname)
                owner = inst.get("DBDNAME")
                if src and src[1]:
                    owner = owner or src[1]
                ref = f"{owner}.{tname}" if owner else tname
                hints.append(builder.ref(ref, "etl_reads", span(inst.line)))
        elif inst_type == "TARGET":
            if shortcut_names and tname in shortcut_names:
                hints.append(builder.ref(f"{folder}.{tname}", "etl_writes", span(inst.line)))
            else:
                hints.append(builder.ref(tname, "etl_writes", span(inst.line)))
        elif inst.get("REUSABLE", "").upper() == "YES" or inst_type == "MAPPLET":
            hints.append(builder.ref(f"{folder}.{tname}", "calls", span(inst.line)))
        else:
            _emit_transformation(inst, folder, f"{qualified}.{inst_name}", builder, mapping_hint)
    for child in node.find_all("TRANSFORMATION"):
        if child.get("REUSABLE", "").upper() != "YES":
            cname = child.get("NAME", "?") or "?"
            _emit_transformation(child, folder, f"{qualified}.{cname}", builder, mapping_hint)
    for conn in node.find_all("CONNECTOR"):
        connectors.append(
            ast.DialectExpr(
                name="informatica.connector",
                args={
                    "from_instance": ast.Identifier(value=conn.get("FROMINSTANCE", "")),
                    "from_field": ast.Identifier(value=conn.get("FROMFIELD", "")),
                    "to_instance": ast.Identifier(value=conn.get("TOINSTANCE", "")),
                    "to_field": ast.Identifier(value=conn.get("TOFIELD", "")),
                },
                span=span(conn.line),
            )
        )
    if connectors:
        args["connectors"] = connectors
    if variables:
        args["variables"] = [ast.Identifier(value=v) for v in variables]
    stmt = ast.DialectStatement(
        name="informatica.mapping",
        args=args,
        references=hints,
        span=span(node.line, node.end_line),
    )
    builder.add(
        "mapplet" if node.local.casefold() == "mapplet" else "mapping",
        qualified,
        stmt,
        node.line,
        node.end_line,
    )


def _emit_transformation(
    node: XmlNode, folder: str, qualified: str, builder: EtlBuilder, hint: str | None = None
) -> None:
    name = qualified.rsplit(".", 1)[-1]
    ttype = node.get("TRANSFORMATION_TYPE") or node.get("TYPE") or ""
    hints: list[ast.RefHint] = []
    args: dict = {"name": ast.Identifier(value=name), "type": ttype or None}
    fields = []
    issues: list[ast.Issue] = []
    for field in node.find_all("TRANSFORMFIELD"):
        expr_text = field.get("EXPRESSION")
        if expr_text:
            expr, expr_issues, expr_calls = parse_expr(expr_text, field.line)
            issues.extend(expr_issues)
            for call in expr_calls:
                target = call.args.get("lookup") or call.args.get("procedure")
                if target is not None:
                    cname = ".".join(p.value for p in target.parts)
                    hints.append(builder.ref(f"{folder}.{cname}", "calls", span(field.line)))
                args.setdefault("call_exprs", []).append(call)
            fields.append(
                ast.DialectExpr(
                    name="informatica.field",
                    args={"name": ast.Identifier(value=field.get("NAME", "")), "expression": expr},
                    span=span(field.line),
                )
            )
        else:
            fields.append(
                ast.DialectExpr(
                    name="informatica.field",
                    args={"name": ast.Identifier(value=field.get("NAME", ""))},
                    span=span(field.line),
                )
            )
    if fields:
        args["fields"] = fields
    for at in node.find_all("TABLEATTRIBUTE") + node.find_all("ATTRIBUTE"):
        attr = (at.get("NAME") or "").strip()
        value = at.get("VALUE") or ""
        if attr in _SQL_ATTRIBUTES and value.strip():
            builder.embed(qualified, value, attr, hint)
        elif attr.casefold() == "lookup table name" and value.strip():
            hints.append(builder.ref(value, "etl_reads", span(at.line)))
        elif attr.casefold() == "stored procedure name" and value.strip():
            hints.append(builder.ref(value, "calls", span(at.line)))
        elif attr.casefold() in {"source filter", "user defined join"} and value.strip():
            if attr.casefold() == "user defined join":
                from migraai.discovery.parsers.ansi import Parser as _AnsiParser

                res = _AnsiParser().parse(f"SELECT * FROM {value}")
                if res.statements and not isinstance(res.statements[0], ast.Unparsed):
                    fexpr, fissues = res.statements[0], []
                else:
                    fexpr, fissues = (
                        ast.Unparsed(
                            text=redact(value), reason="user defined join", span=span(at.line)
                        ),
                        [],
                    )
            else:
                fexpr, fissues, _fcalls = parse_expr(value, at.line)
            if isinstance(fexpr, ast.Unparsed):
                issues.append(
                    ast.Issue(
                        "warning",
                        "informatica.filter_unparsed",
                        f"Could not parse {attr!r} expression.",
                        span(at.line),
                    )
                )
            else:
                issues.extend(fissues)
            args[attr] = fexpr
    if ttype and ttype.casefold() not in _KNOWN_TRANSFORMS:
        issues.append(
            ast.Issue(
                "info",
                "informatica.unknown_transformation_type",
                f"Unknown transformation type {ttype!r}.",
                span(node.line),
            )
        )
    stmt = ast.DialectStatement(
        name="informatica.transformation",
        args=args,
        references=hints,
        span=span(node.line, node.end_line),
    )
    builder.add("transformation", qualified, stmt, node.line, node.end_line, issues)


_KNOWN_TRANSFORMS = {
    "source qualifier",
    "expression",
    "lookup procedure",
    "aggregator",
    "filter",
    "router",
    "joiner",
    "sequence generator",
    "sorter",
    "update strategy",
    "stored procedure",
    "sql transformation",
    "rank",
    "normalizer",
    "union",
    "mapplet",
    "target definition",
    "source definition",
    "custom transformation",
    "transaction control",
    "input transformation",
    "output transformation",
    "sql query",
    "java",
    "xml source qualifier",
}


def _emit_shortcut(node: XmlNode, folder: str, builder: EtlBuilder) -> None:
    name = node.get("NAME", "?") or "?"
    target = f"{node.get('FOLDERNAME', folder)}.{node.get('REFOBJECTNAME', '')}".rstrip(".")
    hints = [builder.ref(target, "synonym_for", span(node.line))] if target else []
    stmt = ast.DialectStatement(
        name="informatica.shortcut",
        args={"name": ast.Identifier(value=name)},
        references=hints,
        span=span(node.line, node.end_line),
    )
    builder.add("shortcut", f"{folder}.{name}", stmt, node.line, node.end_line)


def _emit_scheduler(node: XmlNode, folder: str, builder: EtlBuilder) -> None:
    name = node.get("NAME", "?") or "?"
    stmt = ast.DialectStatement(
        name="informatica.scheduler",
        args={"name": ast.Identifier(value=name)},
        references=[],
        span=span(node.line, node.end_line),
    )
    builder.add("scheduler", f"{folder}.{name}", stmt, node.line, node.end_line)


def _shell_command(
    command: str, owner: str, line: int, hints: list[ast.RefHint], builder: EtlBuilder
) -> None:
    from migraai.etl.shell.parse import extract_invocations, parse_command_line

    script = parse_command_line(command, builder.rel_path, line)
    for invocation in extract_invocations(script.statements, builder.rel_path, script.variables):
        if invocation.kind == "sql" and invocation.text:
            builder.embed(owner, invocation.text, "command", invocation.dialect_hint)
        elif invocation.kind == "ref" and invocation.name:
            hints.append(
                builder.ref(
                    invocation.name,
                    invocation.ref_kind or "etl_runs",
                    span(line),
                    split=invocation.split,
                )
            )
