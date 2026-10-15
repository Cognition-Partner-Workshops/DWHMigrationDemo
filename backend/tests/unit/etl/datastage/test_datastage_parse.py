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
