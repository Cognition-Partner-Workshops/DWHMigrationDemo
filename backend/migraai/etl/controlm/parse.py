"""Control-M parser — DEFTABLE XML and Automation API JSON (design §13)."""

from __future__ import annotations

import json
import re

from migraai.discovery import ast
from migraai.discovery.references import Reference
from migraai.etl.python.common import (
    EtlBuilder,
    EtlParseResult,
    XmlNode,
    decode,
    normalize_path,
    span,
)

_PCT_VAR = re.compile(r"%%([A-Za-z_][A-Za-z_0-9]*)")


def parse(data: bytes, rel_path: str) -> EtlParseResult:
    """Automation API JSON entry point (XML comes in via parse_deftable)."""
    builder = EtlBuilder("controlm", rel_path)
    text = decode(data)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        builder.issue("error", "malformed_json", str(error), span(error.lineno))
        from migraai.etl.python.common import file_unparsed

        builder.objects.append(file_unparsed(rel_path, "malformed JSON", error.lineno))
        return builder.result()
    if not isinstance(payload, dict):
        builder.issue("warning", "unrecognized_json", "Not a Control-M Automation API file.")
        return builder.result()
    _parse_json(payload, text, rel_path, builder)
    if not builder.objects:
        builder.issue("warning", "unrecognized_json", "No Control-M objects found.")
        from migraai.etl.python.common import file_unparsed

        builder.objects.append(file_unparsed(rel_path, "unrecognized JSON"))
    return builder.result()


def parse_deftable(root: XmlNode, builder: EtlBuilder, rel_path: str) -> None:
    """DEFTABLE / FOLDER / SMART_FOLDER roots containing JOB elements."""
    folders = [el for el in _iter(root) if el.local in {"FOLDER", "SMART_FOLDER"}]
    if root.local in {"FOLDER", "SMART_FOLDER"} and root not in folders:
        folders.insert(0, root)
    seen_conditions: set[str] = set()
    for folder_el in folders:
        folder = folder_el.get("FOLDER_NAME") or folder_el.get("NAME") or "folder"
        fvars = _variables(folder_el)
        fstmt = ast.DialectStatement(
            name="controlm.folder",
            args={"folder_name": ast.Identifier(value=folder)},
            references=[],
            span=span(folder_el.line, folder_el.end_line),
        )
        builder.add("folder", folder, fstmt, folder_el.line, folder_el.end_line)
        for sub in folder_el.find_all("SUB_FOLDER"):
            sname = sub.get("FOLDER_NAME") or sub.get("NAME") or "?"
            sstmt = ast.DialectStatement(
                name="controlm.folder",
                args={"folder_name": ast.Identifier(value=sname)},
                references=[],
                span=span(sub.line, sub.end_line),
            )
            builder.add("folder", f"{folder}.{sname}", sstmt, sub.line, sub.end_line)
        for job_el in folder_el.find_all("JOB"):
            _emit_job_xml(job_el, folder, fvars, builder, seen_conditions)


def _iter(node: XmlNode):
    yield node
    for child in node.children:
        yield from _iter(child)


def _variables(el: XmlNode) -> dict[str, str]:
    out = {}
    for var in el.find_all("VARIABLE"):
        name = var.get("NAME")
        if name:
            out[name] = var.get("VALUE", "")
    return out


def _emit_job_xml(
    el: XmlNode,
    folder: str,
    fvars: dict[str, str],
    builder: EtlBuilder,
    seen_conditions: set[str],
) -> None:
    name = el.get("JOBNAME") or el.get("NAME") or "?"
    qualified = f"{folder}.{name}"
    tasktype = (el.get("TASKTYPE") or "").casefold()
    hints: list[ast.RefHint] = []
    issues: list[ast.Issue] = []
    args: dict = {"job_name": ast.Identifier(value=name), "task_type": el.get("TASKTYPE")}
    variables = {**fvars, **_variables(el)}
    schedule = {}
    for key in ("DAYS", "WEEKDAYS", "MONTHS", "TIMEFROM", "TIMETO", "CYCLIC"):
        if el.get(key):
            schedule[key.casefold()] = el.get(key)
    for key in ("DAYSCAL", "WEEKSCAL", "CONFCAL"):
        cal = el.get(key)
        if cal:
            hints.append(builder.ref(cal, "schedules", span(el.line)))
            schedule.setdefault("calendars", []).append(cal)
    if schedule:
        args["schedule"] = ast.DialectExpr(
            name="controlm.schedule",
            args={k: v for k, v in schedule.items()},
            span=span(el.line),
        )
    and_or = []
    for incond in el.find_all("INCOND"):
        cond = incond.get("NAME") or ""
        hints.append(builder.ref(cond, "etl_reads", span(incond.line)))
        and_or.append(incond.get("AND_OR", "AND"))
    for outcond in el.find_all("OUTCOND"):
        cond = outcond.get("NAME") or ""
        if (outcond.get("SIGN") or "+") == "+":
            hints.append(builder.ref(cond, "etl_writes", span(outcond.line)))
            if cond not in seen_conditions:
                seen_conditions.add(cond)
                cstmt = ast.DialectStatement(
                    name="controlm.condition",
                    args={"name": ast.Identifier(value=cond)},
                    references=[],
                    span=span(outcond.line),
                )
                builder.add("condition", cond, cstmt, outcond.line, outcond.line)
        else:
            args.setdefault("outcond_delete", []).append(cond)
    if and_or:
        args["incond_and_or"] = and_or
    if tasktype in {"command", ""}:
        cmdline = el.get("CMDLINE") or ""
        if cmdline:
            _shell_command(
                _sub_vars(cmdline, variables), qualified, el.line, hints, builder, issues
            )
    elif tasktype == "job":
        memlib, memname = el.get("MEMLIB") or "", el.get("MEMNAME") or ""
        if memname:
            path = f"{memlib}/{memname}" if memlib else memname
            hints.append(
                builder.ref(
                    normalize_path(path, builder.rel_path, variables),
                    "etl_runs",
                    span(el.line),
                    split=False,
                )
            )
    else:
        issues.append(
            ast.Issue(
                "warning",
                "controlm.unknown_job_type",
                f"Unknown TASKTYPE {el.get('TASKTYPE')!r}.",
                span(el.line),
            )
        )
    stmt = ast.DialectStatement(
        name="controlm.job", args=args, references=hints, span=span(el.line, el.end_line)
    )
    builder.add("job", qualified, stmt, el.line, el.end_line, issues)


def _sub_vars(text: str, variables: dict[str, str]) -> str:
    return _PCT_VAR.sub(lambda m: variables.get(m.group(1), m.group(0)), text)


def _shell_command(
    command: str,
    owner: str,
    line: int,
    hints: list[ast.RefHint],
    builder: EtlBuilder,
    issues: list[ast.Issue] | None = None,
) -> None:
    from migraai.etl.shell.parse import extract_invocations, parse_command_line

    script = parse_command_line(command, builder.rel_path, line)
    for stmt_ in script.statements:
        if isinstance(stmt_, ast.Unparsed) and issues is not None:
            issues.append(
                ast.Issue("error", "controlm.syntax", stmt_.reason or "Bad CMDLINE.", span(line))
            )
    for invocation in extract_invocations(script.statements, builder.rel_path, script.variables):
        if invocation.kind == "sql" and invocation.text:
            builder.embed(owner, invocation.text, "cmdline", invocation.dialect_hint)
        elif invocation.kind == "ref" and invocation.name:
            hints.append(
                builder.ref(
                    invocation.name,
                    invocation.ref_kind or "etl_runs",
                    span(line),
                    split=invocation.split,
                )
            )


# ---------------------------------------------------------------------------
# Automation API JSON
# ---------------------------------------------------------------------------


def _key_lines(text: str) -> dict[int, str]:
    return dict(enumerate(text.split("\n"), 1))


def _line_of(lines: dict[int, str], key: str, start: int = 1) -> int:
    needle = f'"{key}"'
    for ln in sorted(lines):
        if ln >= start and needle in lines[ln]:
            return ln
    return start


def _parse_json(payload: dict, text: str, rel_path: str, builder: EtlBuilder) -> None:
    lines = _key_lines(text)
    folders: dict[str, int] = {}
    job_names: list[str] = []
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        vtype = str(value.get("Type", ""))
        if vtype.casefold().startswith(("folder", "simplefolder")):
            folders[key] = _line_of(lines, key)
            fstmt = ast.DialectStatement(
                name="controlm.folder",
                args={"folder_name": ast.Identifier(value=key)},
                references=[],
                span=span(folders[key]),
            )
            builder.add("folder", key, fstmt, folders[key], folders[key])
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        vtype = str(value.get("Type", ""))
        line = _line_of(lines, key, 1)
        if vtype.casefold().startswith("calendar"):
            cstmt = ast.DialectStatement(
                name="controlm.calendar",
                args={"name": ast.Identifier(value=key)},
                references=[],
                span=span(line),
            )
            builder.add("calendar", key, cstmt, line, line)
        elif vtype.casefold().startswith("job"):
            job_names.append(key)
    job_objects: dict[str, str] = {}
    for key in job_names:
        value = payload[key]
        folder = str(value.get("ParentFolder") or value.get("Application") or "")
        qualified = f"{folder}.{key}" if folder else key
        job_objects[key] = qualified
    for key in job_names:
        _emit_job_json(key, payload[key], job_objects[key], text, lines, builder)
    # Flow objects → etl_runs edges between jobs
    for key, value in payload.items():
        if isinstance(value, dict) and str(value.get("Type", "")).casefold() == "flow":
            seq = value.get("Sequence") or []
            for a, b in zip(seq, seq[1:], strict=False):
                src_q = job_objects.get(str(a))
                dst_q = job_objects.get(str(b))
                if src_q and dst_q:
                    obj = next((o for o in builder.objects if o.qualified_name == src_q), None)
                    if obj is not None:
                        hint = builder.ref(dst_q, "etl_runs", span(_line_of(lines, key)))
                        obj.node.references.append(hint)
                        obj.references.append(Reference(hint.name, "etl_runs", hint.span))


def _emit_job_json(
    name: str,
    spec: dict,
    qualified: str,
    text: str,
    lines: dict[int, str],
    builder: EtlBuilder,
) -> None:
    jtype = str(spec.get("Type", ""))
    line = _line_of(lines, name)
    hints: list[ast.RefHint] = []
    issues: list[ast.Issue] = []
    args: dict = {"job_name": ast.Identifier(value=name), "job_type": jtype}
    variables = {str(k): str(v) for k, v in (spec.get("Variables") or {}).items()}
    seen_conditions = set()
    for cond in spec.get("eventsToWaitFor") or []:
        hints.append(builder.ref(str(cond), "etl_reads", span(line)))
    deleted = [str(c) for c in spec.get("eventsToDelete") or []]
    if deleted:
        args["events_to_delete"] = deleted
    for cond in spec.get("eventsToAdd") or []:
        hints.append(builder.ref(str(cond), "etl_writes", span(line)))
        if str(cond) not in seen_conditions:
            seen_conditions.add(str(cond))
            cstmt = ast.DialectStatement(
                name="controlm.condition",
                args={"name": ast.Identifier(value=str(cond))},
                references=[],
                span=span(line),
            )
            builder.add("condition", str(cond), cstmt, line, line)
    when = spec.get("When") or {}
    for cal in (
        (when.get("RuleBasedCalendars") or {}).values()
        if isinstance(when.get("RuleBasedCalendars"), dict)
        else (when.get("RuleBasedCalendars") or [])
    ):
        hints.append(builder.ref(str(cal), "schedules", span(line)))
    for cal in spec.get("Calendars") or when.get("Calendars") or []:
        hints.append(builder.ref(str(cal), "schedules", span(line)))
    if when:
        when_args: dict = {}
        for key, val in when.items():
            if isinstance(val, (dict, list)):
                when_args[key.casefold()] = ast.Identifier(value=json.dumps(val, sort_keys=True))
            else:
                when_args[key.casefold()] = ast.Literal(type="string", value=str(val))
        args["schedule"] = ast.DialectExpr(
            name="controlm.schedule",
            args={"when": ast.DialectExpr(name="controlm.when", args=when_args, span=span(line))},
            span=span(line),
        )
    if jtype.endswith(":Command") or jtype == "Job:Command":
        cmd = spec.get("Command") or ""
        if cmd:
            _shell_command(_sub_vars(cmd, variables), qualified, line, hints, builder, issues)
    elif jtype.endswith(":Script"):
        fpath = spec.get("FilePath") or ""
        fname = spec.get("FileName") or ""
        if fname or fpath:
            path = f"{fpath}/{fname}" if fpath else fname
            hints.append(
                builder.ref(
                    normalize_path(path, builder.rel_path, variables),
                    "etl_runs",
                    span(line),
                    split=False,
                )
            )
    elif jtype.endswith(":SQLScript"):
        scr = spec.get("SQLScript") or ""
        if scr:
            hints.append(
                builder.ref(
                    normalize_path(scr, builder.rel_path, variables),
                    "etl_runs",
                    span(line),
                    split=False,
                )
            )
    elif jtype.endswith(":EmbeddedQuery"):
        query = spec.get("Query") or ""
        if query:
            builder.embed(qualified, query, "embedded_query")
    elif jtype not in {"Job", "Job:Command"}:
        issues.append(
            ast.Issue(
                "warning",
                "controlm.unknown_job_type",
                f"Unknown job type {jtype!r}.",
                span(line),
            )
        )
    stmt = ast.DialectStatement(name="controlm.job", args=args, references=hints, span=span(line))
    builder.add("job", qualified, stmt, line, line, issues)
