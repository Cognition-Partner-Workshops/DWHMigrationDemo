"""Informatica parser tests + .xml router (design §4, §13)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.etl.informatica.expr import parse as parse_expr
from migraai.etl.informatica.parse import parse

GOLDEN_DIR = Path(__file__).parent / "golden"


def names(obj):
    return [(r.ref_kind, ".".join(p.value for p in r.name.parts)) for r in obj.references]


FOLDER = b"""<POWERMART><REPOSITORY NAME="r"><FOLDER NAME="DWH">
<SOURCE NAME="ORDERS" DATABASETYPE="Oracle" DBDNAME="src"/>
<MAPPING NAME="m1"><INSTANCE NAME="i1" TYPE="SOURCE" TRANSFORMATION_NAME="ORDERS"/>
<INSTANCE NAME="t1" TYPE="TARGET" TRANSFORMATION_NAME="STG"/></MAPPING>
</FOLDER></REPOSITORY></POWERMART>"""


def test_folder_objects() -> None:
    r = parse(FOLDER, "x.xml")
    m = next(o for o in r.objects if o.type == "mapping")
    assert m.qualified_name == "DWH.m1"
    assert ("etl_reads", "src.ORDERS") in names(m)
    assert ("etl_writes", "STG") in names(m)


def test_workflow_session_scheduler() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F">
<SESSION NAME="s1" MAPPINGNAME="m1"/>
<WORKFLOW NAME="w1"><TASKINSTANCE NAME="s1" TASKTYPE="Session" SCHEDULERNAME="sch"/>
<WORKFLOWLINK FROMTASK="Start" TOTASK="s1" CONDITION="a=b"/></WORKFLOW>
<SCHEDULER NAME="sch1"/></FOLDER></REPOSITORY></POWERMART>"""
    r = parse(data, "x.xml")
    wf = next(o for o in r.objects if o.type == "workflow")
    assert ("etl_runs", "F.s1") in names(wf)
    sess = next(o for o in r.objects if o.type == "session")
    assert ("etl_runs", "F.m1") in names(sess)
    assert any(o.type == "scheduler" for o in r.objects)


def test_session_embeds_sql_and_commands() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F">
<SESSION NAME="s1" MAPPINGNAME="m1"><SESSTRANSFORMATIONINST>
<ATTRIBUTE NAME="Sql Query" VALUE="SELECT a FROM t"/></SESSTRANSFORMATIONINST>
<ATTRIBUTE NAME="Pre-session command" VALUE="sh pre.sh"/></SESSION>
</FOLDER></REPOSITORY></POWERMART>"""
    r = parse(data, "x.xml")
    assert any("SELECT a FROM t" in e.text for e in r.embedded_sql)
    sess = next(o for o in r.objects if o.type == "session")
    assert any(k == "etl_runs" and n.endswith("pre.sh") for k, n in names(sess))


def test_shortcut_synonym_for() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F">
<SHORTCUT NAME="sc" FOLDERNAME="SH" REFOBJECTNAME="m9"/></FOLDER></REPOSITORY></POWERMART>"""
    r = parse(data, "x.xml")
    sc = next(o for o in r.objects if o.type == "shortcut")
    assert ("synonym_for", "SH.m9") in names(sc)


def test_transformation_lookup_calls_and_expression() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F">
<MAPPING NAME="m1"><TRANSFORMATION NAME="exp" TRANSFORMATION_TYPE="Expression">
<TRANSFORMFIELD NAME="o" EXPRESSION="IIF(a&gt;0, :LKP.lkp_names(a), 'x')"/>
</TRANSFORMATION></MAPPING></FOLDER></REPOSITORY></POWERMART>"""
    r = parse(data, "x.xml")
    tr = next(o for o in r.objects if o.type == "transformation")
    assert tr.qualified_name == "F.m1.exp"
    assert ("calls", "F.lkp_names") in names(tr)


def test_unknown_element_and_transformation_type() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F">
<TRANSFORMATION NAME="w" REUSABLE="YES" TRANSFORMATION_TYPE="Bizarro"/>
<WIDGET NAME="u"/></FOLDER></REPOSITORY></POWERMART>"""
    r = parse(data, "x.xml")
    tr = next(o for o in r.objects if o.type == "transformation")
    assert any(i.code == "informatica.unknown_transformation_type" for i in tr.issues)
    assert any(i.code == "informatica.unsupported_element" for i in r.issues)


def test_unrecognized_and_malformed_xml() -> None:
    r = parse(b"<FOO/>", "x.xml")
    assert r.objects[0].status == "unparsed"
    assert any(i.code == "informatica.unrecognized_xml" for i in r.issues)
    for payload in (
        b'<!DOCTYPE x [<!ENTITY e "v">]><x>&e;</x>',
        b'<!DOCTYPE x SYSTEM "file:///etc/passwd"><x/>',
        b"<x><unclosed>",
    ):
        res = parse(payload, "x.xml")
        assert res.objects[0].status == "unparsed"
        assert any(i.severity == "error" for i in res.issues)


def test_expr_precedence() -> None:
    node, _, _ = parse_expr("a + b * c")
    assert type(node).__name__ in {"Binary", "FunctionCall"}
    node2, _, calls = parse_expr("IIF(x, :LKP.f(y), 0)")
    assert [c.name for c in calls] == ["informatica.lookup_call"]


@pytest.mark.parametrize(
    "name,data",
    {
        "mapping": FOLDER,
        "workflow": b"""<POWERMART><REPOSITORY><FOLDER NAME="F"><WORKFLOW NAME="w">
<TASKINSTANCE NAME="s" TASKTYPE="Session"/></WORKFLOW></FOLDER></REPOSITORY></POWERMART>""",
        "session_sql": b"""<POWERMART><REPOSITORY><FOLDER NAME="F">
<SESSION NAME="s" MAPPINGNAME="m">
<SESSTRANSFORMATIONINST><ATTRIBUTE NAME="Sql Query" VALUE="SELECT 1"/></SESSTRANSFORMATIONINST>
</SESSION></FOLDER></REPOSITORY></POWERMART>""",
        "expression": b"""<POWERMART><REPOSITORY><FOLDER NAME="F"><MAPPING NAME="m">
<TRANSFORMATION NAME="e" TRANSFORMATION_TYPE="Expression">
<TRANSFORMFIELD NAME="o" EXPRESSION="DECODE(a,1,'x','y')"/></TRANSFORMATION></MAPPING>
</FOLDER></REPOSITORY></POWERMART>""",
    }.items(),
)
def test_ast_golden(name: str, data: bytes) -> None:
    result = parse(data, "w/x.xml")
    obj = (
        result.objects[0]
        if len(result.objects) == 1
        else next(o for o in result.objects if o.type != "file" and "F." in o.qualified_name)
    )
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


def test_mapplet_objects() -> None:
    r = parse(FOLDER, "x.xml")
    # reuse FOLDER has mapping; add a mapplet-bearing file
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F"><MAPplet NAME="mp1">
<TRANSFORMATION NAME="in_t" TRANSFORMATION_TYPE="Input Transformation"/>
</MAPplet></FOLDER></REPOSITORY></POWERMART>"""
    r = parse(data, "x.xml")
    assert any(o.type == "mapplet" for o in r.objects)


def test_lookup_table_and_stored_proc() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F">
<TRANSFORMATION NAME="lkp" REUSABLE="YES" TRANSFORMATION_TYPE="Lookup Procedure">
<TABLEATTRIBUTE NAME="Lookup table name" VALUE="ref.CATS"/></TRANSFORMATION>
<TRANSFORMATION NAME="sp" REUSABLE="YES" TRANSFORMATION_TYPE="Stored Procedure">
<TABLEATTRIBUTE NAME="Stored Procedure Name" VALUE="etl.AUDIT"/></TRANSFORMATION>
</FOLDER></REPOSITORY></POWERMART>"""
    r = parse(data, "x.xml")
    lkp = next(o for o in r.objects if o.name == "lkp")
    assert ("etl_reads", "ref.CATS") in names(lkp)
    sp = next(o for o in r.objects if o.name == "sp")
    assert ("calls", "etl.AUDIT") in names(sp)


def test_sp_call_expr() -> None:
    node, _, calls = parse_expr("IIF(a, :SP.proc1(x,y), 0)")
    assert calls[0].name == "informatica.sp_call"
    proc = calls[0].args["procedure"]
    assert ".".join(p.value for p in proc.parts) == "proc1"


def test_params_become_param_nodes() -> None:
    from migraai.discovery import ast as _ast

    node, _, _ = parse_expr("$$BatchId + $PMSessionLogDir")
    params = [n for n in node.walk() if isinstance(n, _ast.Param)]
    assert len(params) == 2


def test_routing_xml_to_informatica() -> None:
    from migraai.discovery.pipeline import discover_files

    objects, _edges = discover_files({"f/maps.xml": FOLDER}, "ansi")
    assert any(o.qualified_name == "DWH.m1" for o in objects)


def test_never_executes() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F"><WORKFLOW NAME="w">
<TASK NAME="c" TASKTYPE="Command"><VALUEPAIR NAME="Command" VALUE="touch /tmp/PWNED_INFA"/></TASK>
</WORKFLOW></FOLDER></REPOSITORY></POWERMART>"""
    parse(data, "x.xml")
    assert not Path("/tmp/PWNED_INFA").exists()


def test_never_raises_garbage() -> None:
    for blob in [b"", b"   \x00\x01 garbage", b"<\xff\xfe", b"{" * 50, FOLDER[:60]]:
        result = parse(blob, "x.xml")
        assert result.objects or result.issues


def test_redaction() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F"><SESSION NAME="s" MAPPINGNAME="m">
<ATTRIBUTE NAME="Pre-session command" VALUE="sqlplus u/secretpw@db @x.sql"/>
</SESSION></FOLDER></REPOSITORY></POWERMART>"""
    payload = _dump(parse(data, "x.xml"))
    assert "secretpw" not in payload


def test_expr_precedence_full() -> None:
    # || concat vs comparison vs AND/OR/NOT
    node, _, _ = parse_expr("a || b = c")
    assert type(node).__name__ == "Binary"
    node, _, _ = parse_expr("NOT a AND b OR c")
    assert type(node).__name__ == "Binary"


def test_recovering_partial_workflow() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F">
<WORKFLOW NAME="bad"><WORKFLOWLINK FROMTASK="a" TOTASK="b" CONDITION="IIF(("/></WORKFLOW>
<WORKFLOW NAME="good"><WORKFLOWLINK FROMTASK="x" TOTASK="y"/></WORKFLOW>
</FOLDER></REPOSITORY></POWERMART>"""
    r = parse(data, "x.xml")
    bad = next(o for o in r.objects if o.name == "bad")
    good = next(o for o in r.objects if o.name == "good")
    assert bad.status == "partial" and good.status == "parsed"


def test_shortcut_instance_refs_resolve() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F">
<MAPPING NAME="m"><INSTANCE NAME="i1" TYPE="SOURCE" TRANSFORMATION_NAME="sc_cust"/>
<INSTANCE NAME="i2" TYPE="TARGET" TRANSFORMATION_NAME="sc_mart"/></MAPPING>
<SHORTCUT NAME="sc_cust" FOLDERNAME="G" REFOBJECTNAME="CUST" REFOBJTYPE="Source Definition"/>
<SHORTCUT NAME="sc_mart" FOLDERNAME="G" REFOBJECTNAME="MART" REFOBJTYPE="Target Definition"/>
</FOLDER></REPOSITORY></POWERMART>"""
    r = parse(data, "x.xml")
    m = next(o for o in r.objects if o.name == "m")
    assert ("etl_reads", "F.sc_cust") in names(m)
    assert ("etl_writes", "F.sc_mart") in names(m)


def test_dialect_hint_from_databasetype() -> None:
    data = b"""<POWERMART><REPOSITORY><FOLDER NAME="F">
<SOURCE NAME="ODS_CUST" DATABASETYPE="Oracle" DBDNAME="ods"/>
<MAPPING NAME="m"><INSTANCE NAME="sq" TYPE="SOURCE" TRANSFORMATION_NAME="ODS_CUST"/>
<TRANSFORMATION NAME="SQ_C" TRANSFORMATION_TYPE="Source Qualifier">
<TABLEATTRIBUTE NAME="Sql Query" VALUE="SELECT 1 FROM ODS_CUST"/></TRANSFORMATION></MAPPING>
<SESSION NAME="s" MAPPINGNAME="m">
<ATTRIBUTE NAME="Pre SQL" VALUE="DELETE FROM etl.log"/></SESSION>
</FOLDER></REPOSITORY></POWERMART>"""
    r = parse(data, "x.xml")
    from migraai.discovery.registry import get_sql_parser

    if get_sql_parser("oracle") is not None:
        assert {e.dialect_hint for e in r.embedded_sql} == {"oracle"}
    else:
        # oracle not registered in this build -> no hint per §13 rule
        assert all(e.dialect_hint is None for e in r.embedded_sql)
    # unregistered type -> no hint
    data2 = data.replace(b'DATABASETYPE="Oracle"', b'DATABASETYPE="HiveMQ"')
    r2 = parse(data2, "x.xml")
    assert all(e.dialect_hint is None for e in r2.embedded_sql)
