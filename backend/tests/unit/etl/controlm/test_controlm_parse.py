"""Control-M parser tests — DEFTABLE XML + Automation API JSON (design §13)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.etl.controlm.expr import parse as parse_expr
from migraai.etl.controlm.parse import parse
from migraai.etl.informatica.parse import parse as xml_route

GOLDEN_DIR = Path(__file__).parent / "golden"


def names(obj):
    return [(r.ref_kind, ".".join(p.value for p in r.name.parts)) for r in obj.references]


XML = b"""<DEFTABLE><FOLDER FOLDER_NAME="APP">
<JOB JOBNAME="extract" TASKTYPE="Command" CMDLINE="sh scripts/extract.sh"
  DAYS="ALL" DAYSCAL="WEEKLY_CAL">
<INCOND NAME="ready" ODATE="ODAT" AND_OR="AND"/>
<OUTCOND NAME="extract_done" ODATE="ODAT" SIGN="+"/></JOB>
<JOB JOBNAME="load" TASKTYPE="Job" MEMLIB="lib" MEMNAME="load.sh">
<INCOND NAME="extract_done" ODATE="ODAT"/></JOB>
<SUB_FOLDER FOLDER_NAME="sub"/></FOLDER></DEFTABLE>"""


def test_xml_folder_job_conditions() -> None:
    r = xml_route(XML, "x.xml")
    types = {o.type for o in r.objects}
    assert {"folder", "job", "condition"} <= types
    sub = next(o for o in r.objects if o.type == "folder" and o.name == "sub")
    assert sub.qualified_name == "APP.sub"
    extract = next(o for o in r.objects if o.name == "extract")
    assert ("etl_reads", "ready") in names(extract)
    assert ("etl_writes", "extract_done") in names(extract)
    assert ("schedules", "WEEKLY_CAL") in names(extract)
    assert any(k == "etl_runs" and n.endswith("extract.sh") for k, n in names(extract))
    load = next(o for o in r.objects if o.name == "load")
    assert any(k == "etl_runs" and n.endswith("load.sh") for k, n in names(load))


def test_xml_variables_and_bad_cmdline() -> None:
    data = b"""<DEFTABLE><FOLDER FOLDER_NAME="F">
<VARIABLE NAME="DIR" VALUE="jobs"/>
<JOB JOBNAME="j1" TASKTYPE="Command" CMDLINE="sh %%DIR/run.sh"/>
<JOB JOBNAME="j2" TASKTYPE="Command" CMDLINE='sh "unterminated'/>
<JOB JOBNAME="j3" TASKTYPE="Widget"/>
</FOLDER></DEFTABLE>"""
    r = xml_route(data, "x.xml")
    j1 = next(o for o in r.objects if o.name == "j1")
    assert ("etl_runs", "jobs/run.sh") in names(j1)
    j2 = next(o for o in r.objects if o.name == "j2")
    assert j2.status in {"partial", "parsed"}
    j3 = next(o for o in r.objects if o.name == "j3")
    assert any(i.code == "controlm.unknown_job_type" for i in j3.issues)


JSON = b"""{
"F1": {"Type": "Folder"},
"extract": {"Type": "Job:Command", "ParentFolder": "F1",
  "Command": "sh scripts/extract.sh", "eventsToAdd": ["extract_done"],
  "When": {"RuleBasedCalendars": ["CAL1"]}},
"load": {"Type": "Job:Script", "ParentFolder": "F1", "FilePath": "scripts",
  "FileName": "load.sh", "eventsToWaitFor": ["extract_done"]},
"flow1": {"Type": "Flow", "Sequence": ["extract", "load"]},
"cal": {"Type": "Calendar:Weekly"}
}"""


def test_json_jobs_flow_calendar() -> None:
    r = parse(JSON, "ctm.json")
    types = {o.type for o in r.objects}
    assert {"folder", "job", "condition", "calendar"} <= types
    extract = next(o for o in r.objects if o.name == "extract")
    assert extract.qualified_name == "F1.extract"
    assert ("etl_writes", "extract_done") in names(extract)
    assert ("schedules", "CAL1") in names(extract)
    assert ("etl_runs", "F1.load") in names(extract)  # flow edge
    load = next(o for o in r.objects if o.name == "load")
    assert ("etl_reads", "extract_done") in names(load)
    assert any(k == "etl_runs" and n.endswith("load.sh") for k, n in names(load))


def test_json_embedded_query_and_unknown() -> None:
    data = b"""{"j": {"Type": "Job:Database:EmbeddedQuery", "Query": "SELECT 1"},
"w": {"Type": "Job:Weird"}}"""
    r = parse(data, "x.json")
    assert any("SELECT 1" in e.text for e in r.embedded_sql)
    w = next(o for o in r.objects if o.name == "w")
    assert any(i.code == "controlm.unknown_job_type" for i in w.issues)


def test_expr_precedence() -> None:
    node, _ = parse_expr("a or b and c")
    assert node is not None


@pytest.mark.parametrize(
    "name,data,routed",
    [
        ("deftable", XML, True),
        ("json_jobs", JSON, False),
        (
            "json_flow",
            b'{"f": {"Type":"Folder"}, "a": {"Type":"Job:Command","ParentFolder":"f",'
            b'"Command":"sh x.sh"}, "b": {"Type":"Job:Command","ParentFolder":"f",'
            b'"Command":"sh y.sh"}, "fl": {"Type":"Flow","Sequence":["a","b"]}}',
            False,
        ),
        ("json_calendar", b'{"c": {"Type":"Calendar:Weekly"}}', False),
    ],
)
def test_ast_golden(name: str, data: bytes, routed: bool) -> None:
    result = xml_route(data, "w/x.xml") if routed else parse(data, "w/x.json")
    obj = result.objects[0]
    actual = obj.node.to_dict()
    golden = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1" or not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert json.loads(golden.read_text()) == actual


# ---------------------------------------------------------------------------
# §13 construct coverage + hardening tests
# ---------------------------------------------------------------------------


def _dump(result) -> str:
    return (
        json.dumps([o.to_dict() for o in result.objects])
        + json.dumps([e.text for e in result.embedded_sql])
        + json.dumps([i.message for i in result.issues])
    )


def test_outcond_delete_in_args() -> None:
    data = b"""<DEFTABLE><FOLDER FOLDER_NAME="F">
<JOB JOBNAME="j" TASKTYPE="Command" CMDLINE="sh x.sh">
<OUTCOND NAME="stale" ODATE="ODAT" SIGN="-"/></JOB></FOLDER></DEFTABLE>"""
    r = xml_route(data, "x.xml")
    job = next(o for o in r.objects if o.name == "j")
    assert job.node.args.get("outcond_delete") == ["stale"]
    assert not any(n == "stale" for _, n in names(job))


def test_events_to_delete_json() -> None:
    data = b'{"j": {"Type": "Job:Command", "Command": "sh x.sh", "eventsToDelete": ["e1"]}}'
    r = parse(data, "x.json")
    job = next(o for o in r.objects if o.name == "j")
    from migraai.discovery import ast as _ast

    dele = job.node.args.get("events_to_delete")
    assert isinstance(dele, _ast.ArrayLiteral)
    assert [i.value for i in dele.items] == ["e1"]


def test_sqlscript_and_embedded_dialect() -> None:
    data = b'{"j": {"Type": "Job:Database:SQLScript", "SQLScript": "sql/run.sql"}}'
    r = parse(data, "x.json")
    job = next(o for o in r.objects if o.name == "j")
    assert any(k == "etl_runs" for k, _ in names(job))


def test_no_execution_xml_and_json(tmp_path: Path) -> None:
    data = b"""<DEFTABLE><FOLDER FOLDER_NAME="F">
<JOB JOBNAME="j" TASKTYPE="Command" CMDLINE="touch /tmp/PWNED_CTM"/></FOLDER></DEFTABLE>"""
    xml_route(data, "x.xml")
    parse(b'{"j": {"Type": "Job:Command", "Command": "touch /tmp/PWNED_CTM2"}}', "x.json")
    assert not Path("/tmp/PWNED_CTM").exists()
    assert not Path("/tmp/PWNED_CTM2").exists()


def test_never_raises_garbage() -> None:
    for blob in [b"", b"\x00\xff", b"[1,2", b"{}", JSON[:30], b"<DEFTABLE><unclosed"]:
        result = parse(blob, "x.json") if not blob.startswith(b"<") else xml_route(blob, "x.xml")
        assert result.objects or result.issues


def test_redaction() -> None:
    data = b"""<DEFTABLE><FOLDER FOLDER_NAME="F">
<JOB JOBNAME="j" TASKTYPE="Command" CMDLINE="sqlplus u/secretpw@db @x.sql"/>
</FOLDER></DEFTABLE>"""
    payload = _dump(xml_route(data, "x.xml"))
    assert "secretpw" not in payload


def test_routing_xml_and_json() -> None:
    from migraai.discovery.pipeline import discover_files

    objects, _e = discover_files({"ctm/batch.xml": XML}, "ansi")
    assert any(o.qualified_name == "APP.extract" for o in objects)
    objects2, _e2 = discover_files({"ctm/api.json": JSON}, "ansi")
    assert any(o.qualified_name == "F1.extract" for o in objects2)


def test_bad_cmdline_partial() -> None:
    data = b"""<DEFTABLE><FOLDER FOLDER_NAME="F">
<JOB JOBNAME="bad" TASKTYPE="Command" CMDLINE='sh "unterminated'/>
<JOB JOBNAME="good" TASKTYPE="Command" CMDLINE="sh ok.sh"/>
</FOLDER></DEFTABLE>"""
    r = xml_route(data, "x.xml")
    good = next(o for o in r.objects if o.name == "good")
    assert good.status == "parsed"


def test_expr_precedence_full() -> None:
    n1, _ = parse_expr("a or b and c")
    assert getattr(n1, "op", "").casefold() == "or"
    n2, _ = parse_expr("(a or b) and c")
    assert getattr(n2, "op", "").casefold() == "and"


def test_when_schedule_structured() -> None:
    data = (
        b'{"j": {"Type": "Job:Command", "Command": "sh x.sh",'
        b' "When": {"Months": ["JAN"], "From": "0800"}}}'
    )
    r = parse(data, "x.json")
    job = next(o for o in r.objects if o.name == "j")
    sched = job.node.args.get("schedule")
    assert sched is not None and sched.name == "controlm.schedule"
    when = sched.args["when"]
    assert when.name == "controlm.when" and "months" in when.args


def test_parse_sniffs_xml_directly() -> None:
    r = parse(XML, "x.xml")
    assert any(o.type == "job" for o in r.objects)
    r2 = parse(JSON, "x.json")
    assert any(o.type == "job" for o in r2.objects)


def test_when_nested_collections_typed() -> None:
    from migraai.discovery import ast as _ast

    data = (
        b'{"j": {"Type": "Job:Command", "Command": "sh x.sh",'
        b' "When": {"RuleBasedCalendars": {"MONTH_END": {"Days": ["L1"]}}, "MaxWait": 3}}}'
    )
    r = parse(data, "x.json")
    job = next(o for o in r.objects if o.name == "j")
    when = job.node.args["schedule"].args["when"]
    rbc = when.args["rulebasedcalendars"]
    assert isinstance(rbc, _ast.StructLiteral)
    assert isinstance(when.args["maxwait"], _ast.Literal)
    assert when.args["maxwait"].type == "number"
