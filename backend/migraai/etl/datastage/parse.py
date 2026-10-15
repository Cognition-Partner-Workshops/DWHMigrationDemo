"""DataStage .dsx parser — text DSX and DSExport XML (design §13)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from migraai.discovery import ast
from migraai.etl.datastage.expr import parse as parse_expr
from migraai.etl.python.common import (
    EtlBuilder,
    EtlParseResult,
    XmlNode,
    XmlParseError,
    decode,
    file_unparsed,
    normalize_path,
    parse_xml,
    span,
)

_STAGE_HINT = {
    "oracle": "oracle",
    "db2": "db2",
    "teradata": "teradata",
    "netezza": "netezza",
    "sqlserver": "sqlserver",
    "odbc": None,
}
_SQL_PROPS = {
    "SelectStatement",
    "InsertStatement",
    "UpdateStatement",
    "UserDefinedSQL",
    "BeforeSQL",
    "AfterSQL",
}
_PARAM_REF = re.compile(r"#([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?)#")


@dataclass
class Record:
    identifier: str
    ole_type: str
    name: str
    props: dict[str, str]
    subrecords: list[Record] = field(default_factory=list)
    line: int = 1
    end_line: int = 1
    bad: bool = False


@dataclass
class Job:
    name: str
    job_type: str
    records: list[Record]
    params: dict[str, str]
    line: int
    end_line: int
    bad: bool = False


def parse(data: bytes, rel_path: str) -> EtlParseResult:
    builder = EtlBuilder("datastage", rel_path)
    text = decode(data)
    if "<DSExport" in text[:2000] or text.lstrip().startswith("<"):
        try:
            root = parse_xml(data)
        except XmlParseError as error:
            builder.issue("error", "malformed_xml", str(error), span(error.line))
            builder.objects.append(
                file_unparsed(rel_path, "malformed or forbidden XML", error.line)
            )
            return builder.result()
        parse_dsexport(root, builder, rel_path)
        return builder.result()
    _parse_dsx_text(text, rel_path, builder)
    return builder.result()


# ---------------------------------------------------------------------------
# DSX text
# ---------------------------------------------------------------------------


def _parse_dsx_text(text: str, rel_path: str, builder: EtlBuilder) -> None:
    lines = text.split("\n")
    jobs: list[Job] = []
    current_job: Job | None = None
    current_record: Record | None = None
    current_sub: Record | None = None
    last_key: str | None = None
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith(("//", "#", "$", "*")):
            continue
        if stripped == "BEGIN HEADER":
            while i < len(lines) and lines[i].strip() != "END HEADER":
                i += 1
            i += 1
            continue
        if stripped == "BEGIN DSJOB":
            current_job = Job("", "", [], {}, i, i)
            current_record = None
            continue
        if stripped == "END DSJOB":
            if current_job is not None:
                current_job.end_line = i - 1
                jobs.append(current_job)
            current_job = None
            current_record = None
            continue
        if stripped == "BEGIN DSRECORD":
            if current_job is not None and current_record is not None:
                # previous record never closed → bad, keep it
                current_record.bad = True
                current_record.end_line = i - 1
                current_job.records.append(current_record)
            current_record = Record("", "", "", {}, [], i, i)
            current_sub = None
            continue
        if stripped == "BEGIN DSSUBRECORD":
            current_sub = Record("", "", "", {}, [], i, i)
            continue
        if stripped == "END DSSUBRECORD":
            if current_record is not None and current_sub is not None:
                current_sub.end_line = i - 1
                current_record.subrecords.append(current_sub)
            current_sub = None
            continue
        if stripped == "END DSRECORD":
            if current_job is not None and current_record is not None:
                current_record.end_line = i - 1
                current_job.records.append(current_record)
            current_record = None
            continue
        # `Key =+=+=+=` opens a multi-line value block
        block_key = re.match(r"^(\w+)\s*=\+=\+=\+=\s*$", stripped)
        if block_key and current_job is not None:
            last_key = block_key.group(1)
            chunk = []
            while i < len(lines) and "=+=+=+=" not in lines[i]:
                chunk.append(lines[i])
                i += 1
            i += 1
            target = current_sub or current_record
            if target is not None:
                target.props[last_key] = "".join(chunk).strip()
            continue
        # multi-line =+=+=+= block value
        if stripped.endswith("=+=+=+=") and last_key and current_job is not None:
            chunk = []
            while i < len(lines) and "=+=+=+=" not in lines[i]:
                chunk.append(lines[i])
                i += 1
            i += 1
            target = current_sub or current_record
            if target is not None:
                target.props[last_key] = (target.props.get(last_key, "") or "") + "".join(chunk)
            continue
        m = re.match(r'^(\w+)\s+"(.*)"\s*$', stripped)
        if m and (current_sub or current_record or current_job):
            key, value = m.group(1), m.group(2).replace('\\"', '"')
            last_key = key
            target = current_sub or current_record
            if target is not None:
                if key == "Identifier":
                    target.identifier = value
                elif key == "OLEType":
                    target.ole_type = value
                elif key == "Name":
                    target.name = value
                target.props[key] = value
            elif current_job is not None:
                if key == "Name":
                    current_job.name = value
                elif key == "JobType":
                    current_job.job_type = value
                current_job.params[key] = value
            continue
        if current_job is not None:
            # unparseable line inside a record/job → mark bad
            target = current_sub or current_record
            if target is not None:
                target.bad = True
            else:
                current_job.bad = True
    if current_job is not None:
        if current_record is not None:
            current_record.bad = True
            current_record.end_line = i - 1
            current_job.records.append(current_record)
        current_job.bad = True
        jobs.append(current_job)
    if not jobs:
        builder.issue("warning", "unrecognized", "No DSJOB records found.")
        builder.objects.append(file_unparsed(rel_path, "not a DSX export"))
        return
    for job in jobs:
        _emit_job(job, rel_path, builder)


def _emit_job(job: Job, rel_path: str, builder: EtlBuilder) -> None:
    job_type = {"0": "server", "1": "mainframe", "2": "sequence", "3": "parallel"}.get(
        job.job_type, "job"
    )
    is_sequence = job.job_type == "2"
    issues: list[ast.Issue] = []
    if job.bad or any(rec.bad for rec in job.records):
        issues.append(
            ast.Issue("error", "datastage.syntax", "Malformed DSJOB record.", span(job.line))
        )
    hints: list[ast.RefHint] = []
    links: list[ast.Node] = []
    stages: list[tuple[str, Record]] = []
    inputs: dict[str, int] = {}
    outputs: dict[str, int] = {}
    for rec in job.records:
        ole = rec.ole_type
        if ole in {"CTrxOutput", "CCustomOutput", "CJSActivityOutput", "CCustomInput", "CTrxInput"}:
            partner = rec.props.get("Partner", "")
            columns = []
            for sub in rec.subrecords:
                derivation = sub.props.get("Derivation") or sub.props.get("Expression")
                parsed = parse_expr(derivation, sub.line)[0] if derivation else None
                columns.append(
                    ast.DialectExpr(
                        name="datastage.column",
                        args={
                            "name": ast.Identifier(value=sub.props.get("ColumnName") or sub.name),
                            "derivation": parsed,
                        },
                        span=span(sub.line),
                    )
                )
            trigger = rec.props.get("TriggerExpression") or rec.props.get("Trigger")
            trigger_expr = parse_expr(trigger, rec.line)[0] if trigger else None
            links.append(
                ast.DialectExpr(
                    name="datastage.link",
                    args={
                        "name": ast.Identifier(value=rec.name or rec.identifier),
                        "from_stage": ast.Identifier(value=rec.props.get("Stage", "")),
                        "to_stage": ast.Identifier(value=partner.split("|")[0]),
                        "columns": columns,
                        "trigger": trigger_expr,
                    },
                    span=span(rec.line),
                )
            )
            for piece in partner.split("|"):
                if not piece:
                    continue
                if "Input" in ole:
                    inputs[rec.props.get("Stage", "")] = (
                        inputs.get(rec.props.get("Stage", ""), 0) + 1
                    )
                    outputs.setdefault(piece, 0)
                else:
                    outputs.setdefault(rec.props.get("Stage", ""), 0)
                    inputs[piece] = inputs.get(piece, 0) + 1
            continue
        if (
            ole
            in {
                "CJobParameter",
                "CTrxStage",
                "CCustomStage",
                "CJsJobActivity",
                "CJSJobActivity",
                "CJSExecCmdActivity",
            }
            or "Stage" in ole
            or "Activity" in ole
        ):
            stages.append((rec.name or rec.identifier, rec))
    for stage_name, rec in stages:
        qualified = f"{job.name}.{stage_name}" if job.name else stage_name
        shints: list[ast.RefHint] = []
        sissues: list[ast.Issue] = []
        if rec.bad:
            sissues.append(
                ast.Issue("error", "datastage.syntax", "Malformed DSRECORD.", span(rec.line))
            )
        props = rec.props
        stype = props.get("StageType") or props.get("OleType") or rec.ole_type
        sargs: dict = {"name": ast.Identifier(value=stage_name), "stage_type": stype or None}
        sql_props = {}
        xml_props = props.get("XMLProperties") or ""
        if xml_props and "<" in xml_props:
            try:
                xroot = parse_xml(xml_props.encode())
                for el in _iter(xroot):
                    if el.local in _SQL_PROPS and el.text.strip():
                        sql_props[el.local] = el.text.strip()
                    elif el.local == "TableName" and el.text.strip():
                        props["TableName"] = el.text.strip()
            except XmlParseError:
                pass
        for prop_name, sql_text in sql_props.items():
            sql_text = _PARAM_REF.sub(lambda m: f":{m.group(1).replace('.', '_')}", sql_text)
            builder.embed(qualified, sql_text, prop_name, _dialect_hint(stype))
        if props.get("InputPins"):
            inputs[stage_name] = inputs.get(stage_name, 0) + 1
        if "Connector" in (stype or "") or props.get("TableName"):
            table = props.get("TableName")
            direction = "etl_writes" if inputs.get(stage_name) else "etl_reads"
            if table:
                shints.append(builder.ref(table, direction, span(rec.line)))
        if is_sequence:
            jobname = props.get("Jobname") or props.get("JobName")
            if jobname:
                shints.append(builder.ref(jobname, "etl_runs", span(rec.line)))
            command = props.get("CommandName") or props.get("Command")
            if command:
                cmd = f"{command} {props.get('Parameters', '')}".strip()
                _shell_command(cmd, qualified, rec.line, shints, builder)
        constraint = props.get("Constraint") or props.get("ConstraintExpr")
        if constraint:
            sargs["constraint"] = parse_expr(constraint, rec.line)[0]
        derivations = []
        for sub in rec.subrecords:
            derivation = sub.props.get("Derivation") or sub.props.get("Expression")
            if derivation:
                expr, eissues = parse_expr(derivation, sub.line)
                sissues.extend(eissues)
                derivations.append(
                    ast.DialectExpr(
                        name="datastage.derivation",
                        args={
                            "column": ast.Identifier(value=sub.props.get("ColumnName") or sub.name),
                            "expression": expr,
                        },
                        span=span(sub.line),
                    )
                )
        if derivations:
            sargs["derivations"] = derivations
        stmt = ast.DialectStatement(
            name="datastage.stage",
            args=sargs,
            references=shints,
            span=span(rec.line, rec.end_line),
        )
        builder.add("stage", qualified, stmt, rec.line, rec.end_line, sissues)
    if links:
        job_args_links = links
    else:
        job_args_links = []
    jargs: dict = {"name": ast.Identifier(value=job.name), "job_type": job_type}
    if job_args_links:
        jargs["links"] = job_args_links
    stmt = ast.DialectStatement(
        name="datastage.job", args=jargs, references=hints, span=span(job.line, job.end_line)
    )
    builder.add(
        "sequence" if is_sequence else "job",
        job.name or "job",
        stmt,
        job.line,
        job.end_line,
        issues,
    )


def _dialect_hint(stype: str | None) -> str | None:
    low = (stype or "").casefold()
    for marker, dialect in _STAGE_HINT.items():
        if marker in low:
            return dialect
    return None


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


# ---------------------------------------------------------------------------
# DSExport XML
# ---------------------------------------------------------------------------


def parse_dsexport(
    root: XmlNode, builder: EtlBuilder, rel_path: str, tool_objects: bool = False
) -> None:
    """Map a <DSExport> tree onto the DSX record model and emit objects."""
    for job_el in root.find_all("Job"):
        job = _job_from_xml(job_el)
        _emit_job(job, rel_path, builder)


def _job_from_xml(el: XmlNode) -> Job:
    records: list[Record] = []
    params: dict[str, str] = {}
    name = el.get("Identifier", "") or ""
    job_type = ""
    for rec_el in el.find_all("Record"):
        rec = _record_from_xml(rec_el)
        records.append(rec)
    for rec in records:
        if rec.identifier == "ROOT" or not name:
            if rec.props.get("Name"):
                name = rec.props["Name"]
            job_type = rec.props.get("JobType", job_type)
    return Job(name, job_type, records, params, el.line, el.end_line)


def _record_from_xml(el: XmlNode) -> Record:
    props: dict[str, str] = {}
    subs: list[Record] = []
    for child in el.children:
        if child.local == "Property":
            pname = child.get("Name", "")
            if child.find("Collection") is not None or child.find_all("SubRecord"):
                subs.extend(_record_from_xml(s) for s in child.find_all("SubRecord"))
            else:
                props[pname] = child.text.strip()
        elif child.local == "Collection":
            if child.get("Name", "") in {"InputPins", "OutputPins"}:
                props[child.get("Name", "")] = "true"
            subs.extend(_record_from_xml(s) for s in child.find_all("SubRecord"))
        elif child.local == "SubRecord":
            subs.append(_record_from_xml(child))
    return Record(
        identifier=el.get("Identifier", "") or props.get("Identifier", ""),
        ole_type=el.get("Type", "") or props.get("OLEType", ""),
        name=props.get("Name", ""),
        props=props,
        subrecords=subs,
        line=el.line,
        end_line=el.end_line,
    )


def _iter(node: XmlNode):
    yield node
    for child in node.children:
        yield from _iter(child)


def _norm(path: str, rel_path: str) -> str:
    return normalize_path(path, rel_path)
