"""DataStage parser tests (design §13)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.etl.datastage.expr import parse as parse_expr
from migraai.etl.datastage.parse import parse

GOLDEN_DIR = Path(__file__).parent / "golden"


def names(obj):
    return [(r.ref_kind, ".".join(p.value for p in r.name.parts)) for r in obj.references]


DSX = b"""BEGIN DSJOB
   Identifier "j1"
   OLEType "CJobDefn"
   JobType "0"
   Name "j1"
   BEGIN DSRECORD
      Identifier "V0S1"
      OLEType "CCustomStage"
      Name "ora_src"
      StageType "OracleConnectorPX"
      TableName "orders"
   END DSRECORD
END DSJOB"""

SEQ = b"""BEGIN DSJOB
   Identifier "seq"
   JobType "2"
   Name "seq"
   BEGIN DSRECORD
      Identifier "V0S1"
      OLEType "CJSJobActivity"
      Name "run_load"
      Jobname "j1"
   END DSRECORD
   BEGIN DSRECORD
      Identifier "V0S2"
      OLEType "CJSExecCmdActivity"
      Name "cleanup"
      CommandName "sh"
      Parameters "scripts/clean.sh"
   END DSRECORD
END DSJOB"""


def test_job_and_stage() -> None:
    r = parse(DSX, "x.dsx")
    job = next(o for o in r.objects if o.type == "job")
    assert job.qualified_name == "j1"
    stage = next(o for o in r.objects if o.type == "stage")
    assert stage.qualified_name == "j1.ora_src"
    assert ("etl_reads", "orders") in names(stage)


def test_sequence_objects() -> None:
    r = parse(SEQ, "s.dsx")
    seq = next(o for o in r.objects if o.type == "sequence")
    assert seq.qualified_name == "seq"
    act = next(o for o in r.objects if o.name == "run_load")
    assert ("etl_runs", "j1") in names(act)
    cmd = next(o for o in r.objects if o.name == "cleanup")
    assert any(k == "etl_runs" and n.endswith("clean.sh") for k, n in names(cmd))


def test_user_defined_sql_and_params() -> None:
    data = b"""BEGIN DSJOB
   Identifier "j2"
   JobType "0"
   Name "j2"
   BEGIN DSRECORD
      Identifier "V0S1"
      OLEType "CCustomStage"
      Name "stg"
      StageType "DB2ConnectorPX"
      XMLProperties =+=+=+=
<XMLProperties><TableName>dim</TableName><SelectStatement>SELECT * FROM #P_T#
</SelectStatement></XMLProperties>
      =+=+=+=
   END DSRECORD
END DSJOB"""
    r = parse(data, "x.dsx")
    stage = next(o for o in r.objects if o.type == "stage")
    assert ("etl_reads", "dim") in names(stage)
    assert any("SELECT" in e.text for e in r.embedded_sql)


def test_broken_record_recovers() -> None:
    data = b"""BEGIN DSJOB
   Identifier "jb"
   JobType "0"
   Name "jb"
   BEGIN DSRECORD
      Identifier "bad"
      OLEType "CCustomStage"
      Name "bad"
      @@@garbage line@@@
   BEGIN DSRECORD
      Identifier "good"
      OLEType "CCustomStage"
      Name "good"
   END DSRECORD
END DSJOB"""
    r = parse(data, "x.dsx")
    job = next(o for o in r.objects if o.type == "job")
    assert job.status == "partial"
    good = next(o for o in r.objects if o.name == "good")
    assert good.status == "parsed"


def test_dsexport_xml() -> None:
    data = b"""<DSExport><Header/><Job Identifier="jX">
<Record Identifier="ROOT" Type="JobDefn">
<Property Name="Name">jX</Property><Property Name="JobType">0
</Property>
<Collection Name="Parameters"><SubRecord><Property Name="Name">p1</Property>
</SubRecord></Collection>
</Record>
<Record Identifier="V0S1" Type="CCustomStage"><Property Name="Name">st</Property>
<Property Name="TableName">w1</Property></Record></Job></DSExport>"""
    # routed through informatica.parse
    from migraai.etl.informatica.parse import parse as infa

    r = infa(data, "x.xml")
    job = next(o for o in r.objects if o.type == "job")
    assert job.qualified_name == "jX"
    assert any(o.type == "stage" for o in r.objects)


def test_expr_precedence() -> None:
    node, _ = parse_expr("If a Then b Else c")
    assert node is not None
    node2, _ = parse_expr("x : y + #p#")
    assert node2 is not None


@pytest.mark.parametrize(
    "name,data",
    {
        "job": DSX,
        "sequence": SEQ,
        "stage_table": b"""BEGIN DSJOB
   Identifier "j"
   JobType "0"
   Name "j"
   BEGIN DSRECORD
      Identifier "s"
      OLEType "CCustomStage"
      Name "st"
      StageType "OracleConnectorPX"
      TableName "t"
   END DSRECORD
END DSJOB""",
        "derivation": b"""BEGIN DSJOB
   Identifier "jd"
   JobType "0"
   Name "jd"
   BEGIN DSRECORD
      Identifier "t"
      OLEType "CTrxStage"
      Name "tr"
      BEGIN DSSUBRECORD
         Identifier "c"
         ColumnName "out"
         Derivation "Trim(lnk1.in)"
      END DSSUBRECORD
   END DSRECORD
END DSJOB""",
    }.items(),
)
def test_ast_golden(name: str, data: bytes) -> None:
    result = parse(data, "w/x.dsx")
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


def test_link_columns_and_trigger() -> None:
    data = b"""BEGIN DSJOB
   Identifier "jl"
   JobType "0"
   Name "jl"
   BEGIN DSRECORD
      Identifier "L1"
      OLEType "CTrxOutput"
      Name "lnk1"
      Stage "a"
      Partner "b"
      TriggerExpression "OK And NOT X"
      BEGIN DSSUBRECORD
         Identifier "c1"
         ColumnName "col1"
         Derivation "Trim(a.col1)"
      END DSSUBRECORD
   END DSRECORD
END DSJOB"""
    r = parse(data, "x.dsx")
    job = next(o for o in r.objects if o.type == "job")
    links = job.node.args.get("links")
    assert links and links[0].name == "datastage.link"
    assert links[0].args["columns"][0].args["derivation"] is not None
    assert links[0].args["trigger"] is not None


def test_transformer_constraint_parsed() -> None:
    data = b"""BEGIN DSJOB
   Identifier "jc"
   JobType "0"
   Name "jc"
   BEGIN DSRECORD
      Identifier "t"
      OLEType "CTrxStage"
      Name "tx"
      StageType "CTransformerStage"
      Constraint "AMOUNT > 0 And STATUS = 'A'"
   END DSRECORD
END DSJOB"""
    r = parse(data, "x.dsx")
    stage = next(o for o in r.objects if o.type == "stage")
    assert stage.node.args.get("constraint") is not None


def test_ps_param_becomes_param() -> None:
    from migraai.discovery import ast as _ast

    node, _ = parse_expr("#ps.region# = 'NA'")
    params = [n for n in node.walk() if isinstance(n, _ast.Param)]
    assert params and params[0].text == "@ps.region"


def test_dialect_hints() -> None:
    data = b"""BEGIN DSJOB
   Identifier "jh"
   JobType "0"
   Name "jh"
   BEGIN DSRECORD
      Identifier "s"
      OLEType "CCustomStage"
      Name "st"
      StageType "OracleConnectorPX"
      XMLProperties =+=+=+=
<XMLProperties><SelectStatement>SELECT 1 FROM t</SelectStatement></XMLProperties>
      =+=+=+=
   END DSRECORD
END DSJOB"""
    r = parse(data, "x.dsx")
    assert r.embedded_sql and r.embedded_sql[0].dialect_hint == "oracle"


def test_routing_dsx_and_dsexport() -> None:
    from migraai.discovery.pipeline import discover_files

    objects, _e = discover_files({"jobs/x.dsx": DSX}, "ansi")
    assert any(o.qualified_name == "j1" for o in objects)
    xml = b"""<DSExport><Job Identifier="jx"><Record Identifier="ROOT" Type="JobDefn">
<Property Name="Name">jx</Property><Property Name="JobType">0</Property></Record>
<Record Identifier="s" Type="CCustomStage"><Property Name="Name">st</Property>
<Property Name="TableName">w</Property></Record></Job></DSExport>"""
    objects2, _e2 = discover_files({"exp/x.xml": xml}, "ansi")
    assert any(o.qualified_name == "jx" for o in objects2)


def test_no_execution() -> None:
    data = b"""BEGIN DSJOB
   Identifier "jx"
   JobType "2"
   Name "jx"
   BEGIN DSRECORD
      Identifier "c"
      OLEType "CJSExecCmdActivity"
      Name "cmd"
      CommandName "touch"
      Parameters "/tmp/PWNED_DS"
   END DSRECORD
END DSJOB"""
    parse(data, "x.dsx")
    assert not Path("/tmp/PWNED_DS").exists()


def test_never_raises_garbage() -> None:
    for blob in [b"", b"\x00\x01", b"BEGIN DSJOB\n" + b"garbage\n" * 40, DSX[:40], b"<DSExport"]:
        result = parse(blob, "x.dsx")
        assert result.objects or result.issues


def test_redaction() -> None:
    data = b"""BEGIN DSJOB
   Identifier "jr"
   JobType "2"
   Name "jr"
   BEGIN DSRECORD
      Identifier "c"
      OLEType "CJSExecCmdActivity"
      Name "cmd"
      CommandName "sqlplus u/secretpw@db @x.sql"
   END DSRECORD
END DSJOB"""
    payload = _dump(parse(data, "x.dsx"))
    assert "secretpw" not in payload


def test_expr_precedence_full() -> None:
    n1, _ = parse_expr("a : b + c")
    assert getattr(n1, "op", None) == "||"
    n2, _ = parse_expr("x And y Or Not z")
    assert getattr(n2, "op", "").casefold() == "or"
    n3, _ = parse_expr("If a > b Then x Else y")
    assert type(n3).__name__ == "Case"


def test_stage_direction_via_link_records() -> None:
    # output link record: Stage feeds Partner -> Partner writes
    data = b"""BEGIN DSJOB
   Identifier "jd"
   JobType "0"
   Name "jd"
   BEGIN DSRECORD
      Identifier "s1"
      OLEType "CCustomStage"
      Name "src_t"
      StageType "OracleConnectorPX"
      TableName "ods.t1"
   END DSRECORD
   BEGIN DSRECORD
      Identifier "s2"
      OLEType "CCustomStage"
      Name "tgt_t"
      StageType "DB2ConnectorPX"
      TableName "mart.t2"
   END DSRECORD
   BEGIN DSRECORD
      Identifier "L1"
      OLEType "CTrxOutput"
      Name "lnk"
      Stage "src_t"
      Partner "tgt_t"
   END DSRECORD
END DSJOB"""
    r = parse(data, "x.dsx")
    assert ("etl_reads", "ods.t1") in names(next(o for o in r.objects if o.name == "src_t"))
    assert ("etl_writes", "mart.t2") in names(next(o for o in r.objects if o.name == "tgt_t"))


def test_stage_direction_via_input_link_and_pins() -> None:
    # CTrxInput: Stage receives from Partner -> Stage writes; InputPins prop likewise
    data = b"""BEGIN DSJOB
   Identifier "jp"
   JobType "0"
   Name "jp"
   BEGIN DSRECORD
      Identifier "s1"
      OLEType "CCustomStage"
      Name "src_a"
      StageType "OracleConnectorPX"
      TableName "ods.a"
      OutputPins "tgt_b"
   END DSRECORD
   BEGIN DSRECORD
      Identifier "s2"
      OLEType "CCustomStage"
      Name "tgt_b"
      StageType "DB2ConnectorPX"
      TableName "mart.b"
      InputPins "src_a"
   END DSRECORD
   BEGIN DSRECORD
      Identifier "s3"
      OLEType "CCustomStage"
      Name "tgt_c"
      StageType "ODBCConnectorPX"
      TableName "mart.c"
   END DSRECORD
   BEGIN DSRECORD
      Identifier "L2"
      OLEType "CTrxInput"
      Name "lnk2"
      Stage "tgt_c"
      Partner "src_a"
   END DSRECORD
END DSJOB"""
    r = parse(data, "x.dsx")
    by_name = {o.name: o for o in r.objects}
    assert ("etl_writes", "mart.b") in names(by_name["tgt_b"])
    assert ("etl_writes", "mart.c") in names(by_name["tgt_c"])
    assert ("etl_reads", "ods.a") in names(by_name["src_a"])


def test_dsexport_xml_pins_direction() -> None:
    data = b"""<DSExport><Job Identifier="jx"><Record Identifier="ROOT" Type="JobDefn">
<Property Name="Name">jx</Property><Property Name="JobType">0</Property></Record>
<Record Identifier="s1" Type="CCustomStage"><Property Name="Name">src_x</Property>
<Property Name="StageType">OracleConnectorPX</Property><Property Name="TableName">ods.x</Property>
<Collection Name="OutputPins"><SubRecord>tgt_y</SubRecord></Collection></Record>
<Record Identifier="s2" Type="CCustomStage"><Property Name="Name">tgt_y</Property>
<Property Name="StageType">DB2ConnectorPX</Property><Property Name="TableName">mart.y</Property>
<Collection Name="InputPins"><SubRecord>src_x</SubRecord></Collection></Record></Job></DSExport>"""
    r = parse(data, "x.xml")
    by_name = {o.name: o for o in r.objects}
    assert ("etl_reads", "ods.x") in names(by_name["src_x"])
    assert ("etl_writes", "mart.y") in names(by_name["tgt_y"])


def test_job_parameters_as_dialect_expr() -> None:
    data = b"""BEGIN DSJOB
   Identifier "jpar"
   JobType "0"
   Name "jpar"
   BEGIN DSRECORD
      Identifier "p1"
      OLEType "CJobParameter"
      Name "P_BATCH_DT"
      Prompt "Batch date"
      Default "#NA#"
      ParamType "0"
   END DSRECORD
END DSJOB"""
    r = parse(data, "x.dsx")
    job = next(o for o in r.objects if o.type == "job")
    params = job.node.args.get("parameters")
    assert params and params[0].name == "datastage.parameter"
    assert params[0].args["default"].__class__.__name__ == "Param"
    assert not any(o.name == "P_BATCH_DT" for o in r.objects)


def test_dsexport_xml_parameters() -> None:
    data = b"""<DSExport><Job Identifier="jx"><Record Identifier="ROOT" Type="JobDefn">
<Property Name="Name">jx</Property><Property Name="JobType">0</Property>
<Collection Name="Parameters" Type="Parameters"><SubRecord>
<Property Name="Name">P_REGION</Property>
<Property Name="Default">NA</Property><Property Name="ParamType">0</Property>
</SubRecord></Collection></Record></Job></DSExport>"""
    r = parse(data, "x.xml")
    job = next(o for o in r.objects if o.type == "job")
    params = job.node.args.get("parameters")
    assert params and params[0].name == "datastage.parameter"
    assert params[0].args["default"].__class__.__name__ == "Literal"
