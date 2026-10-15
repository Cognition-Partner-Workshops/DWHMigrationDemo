"""SSIS .dtsx parser (design §13)."""

from __future__ import annotations

import posixpath
import re

from migraai.discovery import ast
from migraai.discovery.references import Reference
from migraai.etl.python.common import (
    EtlBuilder,
    EtlParseResult,
    XmlNode,
    XmlParseError,
    file_unparsed,
    normalize_path,
    parse_xml,
    redact,
    span,
)
from migraai.etl.ssis.expr import parse as parse_expr

_PROVIDER_DIALECT = {
    "sqlncli": "sqlserver",
    "msoledbsql": "sqlserver",
    "sqlclient": "sqlserver",
    "system.data.sqlclient": "sqlserver",
    "oraoledb": "oracle",
    "oracle": "oracle",
    "tdoledb": "teradata",
    "teradata": "teradata",
    "ibmdadb2": "db2",
    "db2oledb": "db2",
    "nzoledb": "netezza",
    "netezza": "netezza",
}
_CONTAINER_TYPES = {"STOCK:SEQUENCE", "STOCK:FORLOOP", "STOCK:FOREACHLOOP"}
_TASK_TYPES = {
    "STOCK:SQLTASK": "sql",
    "SQLTASK:SQLTASK": "sql",
    "STOCK:EXECUTEPACKAGETASK": "exec_package",
    "SSIS.PIPELINE": "dataflow",
    "STOCK:PIPELINE": "dataflow",
    "STOCK:EXECUTEPROCESS": "exec_process",
    "STOCK:SCRIPT": "script",
    "STOCK:FILESYSTEM": "file_system",
}
_KNOWN_COMPONENT = re.compile(
    r"OLE DB|ADO\.NET|ODBC|Flat File|Lookup|Derived Column|Conditional Split|"
    r"OLE DB Command|Union|Sort|Merge|Aggregate|Multicast|Row Count|"
    r"Data Conversion|Script Component|Slowly Changing|Cache Transform|"
    r"Character Map|Copy Column|DataReader|Excel|XML|Audit|Export Column|"
    r"Fuzzy|Import Column|Term Extraction|Term Lookup|Pivot|Unpivot",
    re.IGNORECASE,
)


def parse(data: bytes, rel_path: str) -> EtlParseResult:
    builder = EtlBuilder("ssis", rel_path)
    try:
        root = parse_xml(data)
    except XmlParseError as error:
        builder.issue("error", "malformed_xml", str(error), span(error.line))
        builder.objects.append(file_unparsed(rel_path, "malformed or forbidden XML", error.line))
        return builder.result()
    parse_ssis_root(root, builder, rel_path)
    return builder.result()


def parse_ssis_root(root: XmlNode, builder: EtlBuilder, rel_path: str) -> None:
    """Shared entry for the informatica .xml router (DTS:Executable roots)."""
    pkg_name = (
        _prop(root, "ObjectName")
        or root.get("ObjectName")
        or root.get("CreationName")
        or posixpath.basename(rel_path.replace("\\", "/")).rsplit(".", 1)[0]
    )
    connections: dict[str, XmlNode] = {}
    managers_args = []
    for conn in _find(root, "ConnectionManager"):
        cname = _prop(conn, "ObjectName") or conn.get("ObjectName") or conn.get("Name") or "?"
        connections[cname] = conn
        connstr = _prop(conn, "ConnectionString") or conn.get("ConnectionString") or ""
        provider = _provider(connstr)
        managers_args.append(
            ast.DialectExpr(
                name="ssis.connection",
                args={
                    "name": ast.Identifier(value=cname),
                    "provider": provider or None,
                    "connection_string": ast.Literal(type="string", value=redact(connstr)),
                },
                span=span(conn.line, conn.end_line),
            )
        )
        cstmt = ast.DialectStatement(
            name="ssis.connection_manager",
            args={
                "name": ast.Identifier(value=cname),
                "provider": provider or None,
                "connection_string": ast.Literal(type="string", value=redact(connstr)),
            },
            references=[],
            span=span(conn.line, conn.end_line),
        )
        builder.add("connection_manager", f"{pkg_name}.{cname}", cstmt, conn.line, conn.end_line)

    hints_pkg: list[ast.RefHint] = []
    pkg_args: dict = {"name": ast.Identifier(value=pkg_name)}
    objects_by_path: dict[str, str] = {}

    executables = root.find_all("Executable") or _find(root, "Executable")
    for exe in executables:
        _walk_executable(exe, pkg_name, pkg_name, "", builder, connections, objects_by_path)

    # precedence constraints → etl_runs edges
    constraints = []
    for pc in _find(root, "PrecedenceConstraint"):
        frm = (pc.get("From") or _prop(pc, "From") or "").replace("\\", ".")
        to = (pc.get("To") or _prop(pc, "To") or "").replace("\\", ".")
        src_q = objects_by_path.get(frm) or objects_by_path.get(frm.split(".")[-1])
        dst_q = objects_by_path.get(to) or objects_by_path.get(to.split(".")[-1])
        expr_text = pc.get("Expression") or _prop(pc, "Expression")
        expr = None
        if expr_text:
            expr, _issues = parse_expr(expr_text, pc.line)
        constraints.append(
            ast.DialectExpr(
                name="ssis.precedence",
                args={
                    "from": ast.Identifier(value=frm),
                    "to": ast.Identifier(value=to),
                    "value": pc.get("Value") or _prop(pc, "Value"),
                    "expression": expr,
                },
                span=span(pc.line),
            )
        )
        if src_q and dst_q:
            target_obj = next((o for o in builder.objects if o.qualified_name == src_q), None)
            if target_obj is not None:
                hint = builder.ref(dst_q, "etl_runs", span(pc.line))
                target_obj.node.references.append(hint)
                target_obj.references.append(Reference(hint.name, "etl_runs", hint.span))
    if constraints:
        pkg_args["precedence"] = constraints
    if managers_args:
        pkg_args["connection_managers"] = managers_args
    stmt = ast.DialectStatement(
        name="ssis.package",
        args=pkg_args,
        references=hints_pkg,
        span=span(root.line, root.end_line),
    )
    builder.add("package", pkg_name, stmt, root.line, root.end_line)


def _find(node: XmlNode, local: str):
    for el in _iter(node):
        if el.local == local:
            yield el


def _iter(node: XmlNode):
    yield node
    for child in node.children:
        yield from _iter(child)


def _prop(node: XmlNode, name: str) -> str | None:
    """A DTS:Property child element's text, or an attribute fallback."""
    for child in node.children:
        if child.local == "Property" and (child.get("Name") or "").casefold() == name.casefold():
            text = child.text.strip()
            if text:
                return text
    for el in _iter(node):
        if el.local == "Property" and (el.get("Name") or "").casefold() == name.casefold():
            text = el.text.strip()
            if text:
                return text
    return node.get(name)


def _provider(connstr: str) -> str | None:
    low = connstr.casefold()
    for marker, dialect in _PROVIDER_DIALECT.items():
        if marker in low:
            return dialect
    return None


def _clean_table(text: str) -> str:
    return re.sub(r"[\[\]]", "", text.strip())


def _walk_executable(
    exe: XmlNode,
    pkg: str,
    prefix: str,
    path: str,
    builder: EtlBuilder,
    connections: dict[str, XmlNode],
    objects_by_path: dict[str, str],
) -> None:
    creation = (
        exe.get("ExecutableType")
        or exe.get("CreationName")
        or _prop(exe, "ExecutableType")
        or _prop(exe, "CreationName")
        or ""
    )
    name = _prop(exe, "ObjectName") or exe.get("ObjectName") or exe.get("Name") or "?"
    qualified = f"{pkg}.{path}{name}" if path else f"{pkg}.{name}"
    objects_by_path.setdefault(name, qualified)
    objects_by_path.setdefault(f"{pkg}\\{name}".replace("\\", "."), qualified)
    up = creation.upper()
    kind = _TASK_TYPES.get(up, _TASK_TYPES.get(creation, ""))
    if not kind and up.startswith("SSIS.PIPELINE"):
        kind = "dataflow"
    if creation.upper() in _CONTAINER_TYPES:
        hints: list[ast.RefHint] = []
        stmt = ast.DialectStatement(
            name="ssis.container",
            args={"name": ast.Identifier(value=name), "type": creation},
            references=hints,
            span=span(exe.line, exe.end_line),
        )
        builder.add("container", qualified, stmt, exe.line, exe.end_line)
        for child in exe.find_all("Executable"):
            _walk_executable(
                child, pkg, prefix, f"{path}{name}.", builder, connections, objects_by_path
            )
        return
    hints = []
    args: dict = {"name": ast.Identifier(value=name), "type": creation or None}
    issues: list[ast.Issue] = []
    task_kind = kind or "unknown"
    if task_kind == "unknown" and not creation:
        task_kind = "task"
    if task_kind == "unknown" and creation:
        issues.append(
            ast.Issue(
                "warning", "ssis.unknown_task", f"Unknown task type {creation!r}.", span(exe.line)
            )
        )
    if task_kind == "sql":
        stmt_text = None
        for el in _iter(exe):
            if el.local == "SqlStatementSource" and el.text.strip():
                stmt_text = el.text.strip()
        for el in _iter(exe):
            if el.local in {"SqlStatementSourceType", "SQLTaskData"}:
                pass
        if stmt_text:
            dialect = None
            conn_ref = _prop(exe, "Connection") or exe.get("Connection")
            if conn_ref and conn_ref in connections:
                dialect = _provider(
                    _prop(connections[conn_ref], "ConnectionString")
                    or connections[conn_ref].get("ConnectionString")
                    or ""
                )
            builder.embed(qualified, stmt_text, "sql", dialect)
    elif task_kind == "exec_package":
        child_pkg = None
        conn_ref = None
        for el in _iter(exe):
            if el.local in {"PackageName", "PackageFile"} and (el.text.strip() or el.get("Name")):
                child_pkg = el.text.strip() or el.get("Name")
            if el.local == "UseProjectReference":
                pass
            if el.local == "Connection" and (el.text.strip() or el.get("Name")):
                conn_ref = el.text.strip() or el.get("Name")
        if child_pkg is None:
            for el in _iter(exe):
                if (el.get("Name") or "").casefold() in {
                    "package",
                    "packagename",
                } and el.text.strip():
                    child_pkg = el.text.strip()
        if child_pkg:
            stem = posixpath.basename(child_pkg.replace("\\", "/")).rsplit(".", 1)[0]
            hints.append(builder.ref(stem, "etl_runs", span(exe.line)))
        elif conn_ref:
            hints.append(builder.ref(conn_ref, "etl_runs", span(exe.line), split=False))
    elif task_kind == "dataflow":
        _parse_dataflow(exe, qualified, builder, connections)
    for el in _iter(exe):
        if el.local == "PropertyExpression" and el.text.strip():
            expr, _i = parse_expr(el.text.strip(), el.line)
            args.setdefault("property_expressions", []).append(
                ast.DialectExpr(
                    name="ssis.property_expression",
                    args={"name": el.get("Name"), "expression": expr},
                    span=span(el.line),
                )
            )
    stmt = ast.DialectStatement(
        name="ssis.task", args=args, references=hints, span=span(exe.line, exe.end_line)
    )
    builder.add("task", qualified, stmt, exe.line, exe.end_line, issues)


def _parse_dataflow(
    exe: XmlNode, owner: str, builder: EtlBuilder, connections: dict[str, XmlNode]
) -> None:
    for comp in _iter(exe):
        if comp.local not in {"component", "Component"}:
            continue
        name = comp.get("name") or comp.get("Name") or "?"
        class_id = (
            comp.get("componentClassID")
            or comp.get("ComponentClassID")
            or comp.get("description")
            or ""
        )
        qualified = f"{owner}.{name}"
        hints: list[ast.RefHint] = []
        args: dict = {"name": ast.Identifier(value=name), "component_class": class_id or None}
        issues: list[ast.Issue] = []
        low = class_id.casefold()
        if "source" in low or "destination" in low or "lookup" in low or "command" in low:
            is_dest = "destination" in low or low.endswith("command")
            for el in _iter(comp):
                if el.local not in {"property", "Property"}:
                    continue
                pname = (el.get("name") or el.get("Name") or "").casefold()
                text = el.text.strip()
                if not text:
                    continue
                if pname in {"openrowset", "tableorviewname"}:
                    hints.append(
                        builder.ref(
                            _clean_table(text),
                            "etl_writes" if is_dest else "etl_reads",
                            span(el.line),
                        )
                    )
                elif pname == "sqlcommand":
                    builder.embed(qualified, text, "component_sql")
            conn_ref = comp.get("connectionManagerID") or comp.get("ConnectionManagerID")
            if conn_ref and conn_ref in connections and "file" in low + "flat":
                path = _prop(connections[conn_ref], "ConnectionString") or ""
                if path:
                    hints.append(
                        builder.ref(
                            normalize_path(path, builder.rel_path),
                            "etl_writes" if is_dest else "etl_reads",
                            span(comp.line),
                            split=False,
                        )
                    )
        if class_id and not _KNOWN_COMPONENT.search(class_id):
            issues.append(
                ast.Issue(
                    "warning",
                    "ssis.unknown_component",
                    f"Unknown data flow component {class_id!r}.",
                    span(comp.line),
                )
            )
        stmt = ast.DialectStatement(
            name="ssis.dataflow_component",
            args=args,
            references=hints,
            span=span(comp.line, comp.end_line),
        )
        builder.add("dataflow_component", qualified, stmt, comp.line, comp.end_line, issues)
