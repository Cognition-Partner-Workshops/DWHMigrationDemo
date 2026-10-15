from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.etl.spark.parse import parse
from migraai.etl.spark.scala import ScKind, tokenize_scala

GOLDEN_DIR = Path(__file__).parent / "golden"


def names(obj):
    return [(r.ref_kind, ".".join(p.value for p in r.name.parts)) for r in obj.references]


def _py(text: str) -> object:
    return parse(text.encode(), "jobs/etl_job.py")


def test_pyspark_read_write_paths() -> None:
    result = _py(
        """from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
def run(spark):
    spark.sql("SELECT * FROM mart.fact_orders")
    a = spark.table("sales.orders")
    b = spark.read.table("dwh.dim_dates")
    a.write.saveAsTable("mart.out1")
    a.write.insertInto("mart.out2", overwrite=True)
    a.writeTo("mart.out3")
    r = spark.read.parquet("/data/in/orders")
    a.write.parquet("/data/out/orders")
    spark.read.json("/data/in/ev.json")
    a.write.mode("append").save("/data/out/eod")
"""
    )
    fn = next(o for o in result.objects if o.type == "function")
    refs = names(fn)
    for expected in [
        ("etl_reads", "sales.orders"),
        ("etl_reads", "dwh.dim_dates"),
        ("etl_writes", "mart.out1"),
        ("etl_writes", "mart.out2"),
        ("etl_writes", "mart.out3"),
        ("etl_reads", "/data/in/orders"),
        ("etl_writes", "/data/out/orders"),
        ("etl_reads", "/data/in/ev.json"),
        ("etl_writes", "/data/out/eod"),
    ]:
        assert expected in refs, expected
    assert any(e.text.startswith("SELECT") for e in result.embedded_sql)


def test_jdbc_and_query_option() -> None:
    result = _py(
        """def load(spark):
    a = spark.read.format("jdbc").option("url", "jdbc:x").option("dbtable", "src.accounts").load()
    b = spark.read.format("jdbc").option("query", "SELECT id FROM src.q").load()
    b.write.format("jdbc").option("dbtable", "mart.acct").save()
    c = spark.read.jdbc("jdbc:x", "src.direct")
"""
    )
    fn = next(o for o in result.objects if o.type == "function")
    refs = names(fn)
    assert ("etl_reads", "src.accounts") in refs
    assert ("etl_reads", "src.direct") in refs
    assert any(e.text.startswith("SELECT id") for e in result.embedded_sql)


def test_deltatable_forname() -> None:
    result = _py(
        "from delta import DeltaTable\ndef f(spark):\n    DeltaTable.forName(spark, 'dwh.t')\n"
    )
    fn = next(o for o in result.objects if o.type == "function")
    assert ("etl_reads", "dwh.t") in names(fn)


def test_scala_tokenizer() -> None:
    tokens = tokenize_scala(
        'package a.b\n// c\n/* d */\nobject O { val s = """multi\nline"""\n'
        "val i = s\"interp\"\nval c = 'x'\n}"
    )
    assert any(t.kind is ScKind.STRING and "multi" in t.text for t in tokens)
    assert not any(t.kind is ScKind.IDENT and t.text == "c" for t in tokens[:3])


def test_scala_objects() -> None:
    result = parse(
        b"""package com.acme.etl
object OrdersJob {
  def main(args: Array[String]): Unit = {
    spark.sql("SELECT * FROM raw.events")
    val df = spark.read.table("src.orders")
    df.write.saveAsTable("mart.orders")
    spark.read.parquet("/data/in")
    spark.read.format("jdbc").option("dbtable", "src.acct").load()
    DeltaTable.forName(spark, "dwh.dt")
  }
  def helper(): Int = { spark.table("util.helper_t"); 1 }
}
""",
        "src/OrdersJob.scala",
    )
    by = {o.qualified_name: o for o in result.objects}
    obj = by["com.acme.etl.OrdersJob"]
    assert obj.type == "scala_object"
    main = by["com.acme.etl.OrdersJob.main"]
    refs = names(main)
    for expected in [
        ("etl_reads", "src.orders"),
        ("etl_writes", "mart.orders"),
        ("etl_reads", "src.acct"),
        ("etl_reads", "dwh.dt"),
    ]:
        assert expected in refs, expected
    assert ("etl_reads", "util.helper_t") in names(by["com.acme.etl.OrdersJob.helper"])
    embedded = [e for e in result.embedded_sql if e.owner_qualified_name.endswith("main")]
    assert embedded and "raw.events" in embedded[0].text


def test_json_livy_submit() -> None:
    result = parse(
        json.dumps(
            {
                "file": "jobs/etl.py",
                "className": "com.acme.etl.Main",
                "args": ["2024-01-01"],
                "conf": {"spark.executor.memory": "4g"},
            }
        ).encode(),
        "conf/submit.json",
    )
    (obj,) = result.objects
    assert obj.type == "spark_submit"
    assert ("etl_runs", "conf/jobs/etl.py") in names(obj)
    assert ("etl_runs", "com.acme.etl.Main") in names(obj)


def test_json_controlm_delegation_unavailable() -> None:
    result = parse(
        json.dumps({"Folder1": {"Type": "Folder"}, "cal": {"Type": "Calendar"}}).encode(),
        "ctm/export.json",
    )
    assert result.objects == []
    assert any(i.code == "spark.controlm_unavailable" for i in result.issues)


def test_json_malformed() -> None:
    result = parse(b"{not json", "bad.json")
    assert result.objects[0].type == "file"
    assert result.objects[0].status == "unparsed"


def test_json_other_shape() -> None:
    result = parse(json.dumps({"a": 1}).encode(), "cfg.json")
    # not a submit config — still produces spark_submit or warns; must not raise
    assert result.objects or result.issues


@pytest.mark.parametrize(
    "data",
    [b"", b"\x00\xff garbage \x80", b"[[[", b"class", b"{" * 500],
)
def test_never_raises(data: bytes) -> None:
    assert parse(data, "g/x.scala") is not None
    assert parse(data, "g/x.json") is not None
    assert parse(data, "g/x.py") is not None


def test_ast_golden() -> None:
    result = _py("def f(spark):\n    spark.table('a.b')\n    spark.sql('SELECT 1')\n")
    fn = next(o for o in result.objects if o.type == "function")
    actual = fn.node.to_dict()
    golden = GOLDEN_DIR / "spark_function.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1" or not golden.exists():
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert json.loads(golden.read_text()) == actual
