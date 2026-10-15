"""AST goldens for representative spark constructs (§10)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.etl.spark.parse import parse

GOLDEN_DIR = Path(__file__).parent / "golden"

CASES = {
    "spark_sql": (
        b"from pyspark.sql import SparkSession\ndef f(spark):\n    spark.sql('SELECT 1 FROM t')\n",
        "j/x.py",
        "function",
    ),
    "jdbc_chain": (
        b"""def f(spark):
    df = spark.read.format('jdbc').option('dbtable', 's.t').load()
    df.write.format('jdbc').option('dbtable', 's.w').save()
""",
        "j/x.py",
        "function",
    ),
    "scala_object": (
        b"""package a.b
object J {
  def main(): Unit = { spark.read.table('s.t').write.saveAsTable('m.w') }
}
""",
        "j/J.scala",
        "scala_object",
    ),
    "spark_submit_json": (
        b'{"file": "jobs/x.py", "className": "com.a.M", "conf": {"k": "v"}}',
        "conf/x.json",
        "spark_submit",
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_ast_golden(name: str) -> None:
    data, rel_path, obj_type = CASES[name]
    result = parse(data, rel_path)
    obj = next(o for o in result.objects if o.type == obj_type)
    actual = obj.node.to_dict()
    golden = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1" or not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert json.loads(golden.read_text()) == actual
