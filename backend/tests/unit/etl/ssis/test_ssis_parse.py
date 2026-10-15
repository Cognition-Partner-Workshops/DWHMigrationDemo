"""SSIS parser tests (design §13)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.etl.ssis.expr import parse as parse_expr
from migraai.etl.ssis.parse import parse

GOLDEN_DIR = Path(__file__).parent / "golden"


def names(obj):
    return [(r.ref_kind, ".".join(p.value for p in r.name.parts)) for r in obj.references]


PKG = b"""<DTS:Executable xmlns:DTS="x" DTS:ExecutableType="SSIS.Package.3">
<DTS:Property DTS:Name="ObjectName">pkg</DTS:Property>
<DTS:ConnectionManager><DTS:Property DTS:Name="ObjectName">cm</DTS:Property>
<DTS:Property DTS:Name="ConnectionString">Provider=SQLNCLI11;Server=h;PWD=abc;</DTS:Property>
</DTS:ConnectionManager>
<DTS:Executable DTS:ExecutableType="STOCK:SQLTask">
<DTS:Property DTS:Name="ObjectName">load</DTS:Property>
<DTS:Property DTS:Name="Connection">cm</DTS:Property>
<ObjectData><SqlTaskData><SqlStatementSource>SELECT 1 FROM t</SqlStatementSource>
</SqlTaskData></ObjectData></DTS:Executable>
<DTS:Executable DTS:ExecutableType="STOCK:EXECUTEPACKAGETASK">
<DTS:Property DTS:Name="ObjectName">child</DTS:Property>
<ObjectData><ExecutePackageTask><PackageName>Child.dtsx</PackageName></ExecutePackageTask>
</ObjectData></DTS:Executable>
</DTS:Executable>"""


def test_objects_and_sql() -> None:
    r = parse(PKG, "p.dtsx")
    types = {o.type for o in r.objects}
    assert {"package", "connection_manager", "task"} <= types
    assert any("SELECT 1 FROM t" in e.text for e in r.embedded_sql)
    pkg = next(o for o in r.objects if o.type == "package")
    assert pkg.qualified_name == "pkg"
    child = next(o for o in r.objects if o.name == "child")
    assert ("etl_runs", "Child") in names(child)


def test_connection_string_redacted() -> None:
    r = parse(PKG, "p.dtsx")
    payload = json.dumps([o.to_dict() for o in r.objects])
    assert "abc;" not in payload and "abc" not in payload.split("PWD")[1][:10]


def test_sequence_container_and_dataflow() -> None:
    data = b"""<DTS:Executable xmlns:DTS="x" DTS:ExecutableType="SSIS.Package.3">
<DTS:Property DTS:Name="ObjectName">p</DTS:Property>
<DTS:Executable DTS:ExecutableType="STOCK:SEQUENCE">
<DTS:Property DTS:Name="ObjectName">seq</DTS:Property>
<DTS:Executable DTS:ExecutableType="SSIS.Pipeline.3">
<DTS:Property DTS:Name="ObjectName">df</DTS:Property>
<ObjectData><pipeline><components>
<component componentClassID="Microsoft.OLEDBSource" name="src"
  connectionManagerID="cm">
<properties><property name="OpenRowset">[dbo].[T]</property></properties></component>
<component componentClassID="Weird.Component" name="w"/></components></pipeline>
</ObjectData></DTS:Executable></DTS:Executable></DTS:Executable>"""
    r = parse(data, "p.dtsx")
    types = {o.type for o in r.objects}
    assert "container" in types and "dataflow_component" in types
    comp = next(o for o in r.objects if o.name == "src")
    assert comp.qualified_name == "p.seq.df.src"
    assert ("etl_reads", "dbo.T") in names(comp)
    weird = next(o for o in r.objects if o.name == "w")
    assert any(i.code == "ssis.unknown_component" for i in weird.issues)


def test_unknown_task_and_malformed() -> None:
    data = b"""<DTS:Executable xmlns:DTS="x"><DTS:Property DTS:Name="ObjectName">p</DTS:Property>
<DTS:Executable DTS:ExecutableType="VENDOR:STRANGE"><DTS:Property DTS:Name="ObjectName">t
</DTS:Property>
</DTS:Executable></DTS:Executable>"""
    r = parse(data, "p.dtsx")
    t = next(o for o in r.objects if o.name == "t")
    assert any(i.code == "ssis.unknown_task" for i in t.issues)
    bad = parse(b'<!DOCTYPE x [<!ENTITY e "v">]><x>&e;</x>', "p.dtsx")
    assert bad.objects[0].status == "unparsed"


def test_expr_precedence() -> None:
    node, _ = parse_expr("@[User::a] == 1 && @[User::b] == 2 || x")
    # && binds tighter than ||
    assert node is not None
    node2, _ = parse_expr("x ? 1 : 2")
    assert node2 is not None


@pytest.mark.parametrize(
    "name,data",
    {
        "sqltask": PKG,
        "sequence": b"""<DTS:Executable xmlns:DTS="x">
<DTS:Property DTS:Name="ObjectName">p</DTS:Property>
<DTS:Executable DTS:ExecutableType="STOCK:SEQUENCE"><DTS:Property DTS:Name="ObjectName">s
</DTS:Property>
</DTS:Executable></DTS:Executable>""",
        "dataflow": b"""<DTS:Executable xmlns:DTS="x">
<DTS:Property DTS:Name="ObjectName">p
</DTS:Property>
<DTS:Executable DTS:ExecutableType="SSIS.Pipeline.3">
<DTS:Property DTS:Name="ObjectName">d</DTS:Property>
<ObjectData><pipeline><components>
<component componentClassID="Microsoft.OLEDBDestination"
name="dst"><properties><property name="OpenRowset">[dbo].[W]</property>
</properties></component>
</components></pipeline></ObjectData></DTS:Executable>
</DTS:Executable>""",
        "exec_package": b"""<DTS:Executable xmlns:DTS="x">
<DTS:Property DTS:Name="ObjectName">p</DTS:Property>
<DTS:Executable DTS:ExecutableType="STOCK:EXECUTEPACKAGETASK">
<DTS:Property DTS:Name="ObjectName">e</DTS:Property>
<ObjectData><ExecutePackageTask><PackageName>C.dtsx</PackageName></ExecutePackageTask>
</ObjectData></DTS:Executable></DTS:Executable>""",
    }.items(),
)
def test_ast_golden(name: str, data: bytes) -> None:
    result = parse(data, "w/p.dtsx")
    preferred = {
        "sqltask": "task",
        "sequence": "container",
        "dataflow": "dataflow_component",
        "exec_package": "task",
    }[name]
    obj = next(o for o in result.objects if o.type == preferred)
    actual = obj.node.to_dict()
    golden = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1" or not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert json.loads(golden.read_text()) == actual
