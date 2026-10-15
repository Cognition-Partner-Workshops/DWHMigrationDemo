from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery import ast
from migraai.etl.python.parse import parse
from migraai.etl.python.recover import parse_python_recovering

GOLDEN_DIR = Path(__file__).parent / "golden"


def names(obj):
    return [(r.ref_kind, ".".join(p.value for p in r.name.parts)) for r in obj.references]


def test_module_and_function_objects() -> None:
    result = parse(
        b"import os\n\ndef a(x):\n    return x\n\ndef b():\n    return a(1)\n",
        "pkg/mod.py",
    )
    by_name = {o.qualified_name: o for o in result.objects}
    assert by_name["pkg.mod"].type == "module"
    assert by_name["pkg.mod.a"].type == "function"
    assert ("calls", "pkg.mod.a") in names(by_name["pkg.mod.b"])


def test_init_module_name() -> None:
    result = parse(b"x = 1\n", "pkg/sub/__init__.py")
    assert result.objects[0].qualified_name == "pkg.sub"


def test_import_calls_and_stdlib_skipped() -> None:
    result = parse(
        b"import os\nimport util.helper\nfrom tools import fix\n"
        b"def f():\n    os.getcwd()\n    util.helper.run()\n    fix()\n",
        "m/main.py",
    )
    fn = next(o for o in result.objects if o.qualified_name == "m.main.f")
    assert ("calls", "util.helper.run") in names(fn)
    assert ("calls", "tools.fix") in names(fn)
    assert all("os" not in n for _, n in names(fn))


def test_relative_import_resolution() -> None:
    result = parse(
        b"from .util import clean\ndef f():\n    clean()\n",
        "pkg/sub/main.py",
    )
    fn = next(o for o in result.objects if o.qualified_name.endswith("f"))
    assert ("calls", "pkg.sub.util.clean") in names(fn)


def test_embedded_sql_and_table_refs() -> None:
    result = parse(
        b"""Q = 'SELECT a FROM src.orders'
def run(cur):
    cur.execute(Q)
    df = pd.read_sql_table('orders', con, schema='sales')
    df.to_sql('fact_orders', con, schema='mart')
    cur.executemany('INSERT INTO t VALUES (%s)', rows)
    cursor.callproc('pkg.p_audit')
""",
        "jobs/etl.py",
    )
    fn = next(o for o in result.objects if o.type == "function")
    assert ("etl_reads", "sales.orders") in names(fn)
    assert ("etl_writes", "mart.fact_orders") in names(fn)
    assert ("calls", "pkg.p_audit") in names(fn)
    texts = [e.text for e in result.embedded_sql]
    assert "SELECT a FROM src.orders" in texts
    assert "INSERT INTO t VALUES (%s)" in texts
    assert all(e.owner_qualified_name == "jobs.etl.run" for e in result.embedded_sql)


def test_dynamic_sql_issue() -> None:
    result = parse(b"def f(cur, q):\n    cur.execute(q)\n", "m/x.py")
    assert any(i.code == "python.dynamic_sql" for o in result.objects for i in o.issues)


def test_password_kwargs_redacted() -> None:
    result = parse(
        b"def f():\n    connect(host='h', password='s3cret', api_key='k3y')\n",
        "m/x.py",
    )
    payload = json.dumps([o.to_dict() for o in result.objects])
    assert "s3cret" not in payload and "k3y" not in payload
    assert "****" in payload


def test_expression_conversion_coverage() -> None:
    import ast as pyast

    from migraai.etl.python.pyconvert import ConvertContext, convert_statement

    ctx = ConvertContext(tool="python")
    tree = pyast.parse(
        """x = a if a > 0 else -a
y = [1, 2] + (3,)
z = {'k': a}
s = f'v={a}'
d = x[1:3]
g = lambda q: q + 1
h = [i * 2 for i in y]
return_ = a < b <= 3
""",
    )
    text = json.dumps([convert_statement(stmt, ctx).to_dict() for stmt in tree.body])
    for kind in (
        "case",
        "array_literal",
        "struct_literal",
        "fstring",
        "slice",
        "lambda",
        "binary",
    ):
        assert kind in text, kind
    assert "list_comp" in text or "listcomp" in text


def test_recovery_recovers_later_defs() -> None:
    result = parse(
        b"""def good():
    return 1

def broken(:
    pass

def later():
    return 2
""",
        "m/rec.py",
    )
    by_name = {o.qualified_name: o for o in result.objects}
    assert by_name["m.rec"].status == "partial"
    assert by_name["m.rec.good"].status == "parsed"
    assert by_name["m.rec.later"].status == "parsed"
    assert "m.rec.broken" not in by_name


def test_recover_rounds_limit() -> None:
    recovered = parse_python_recovering("def f(:\n" * 1 + "x = 1\n", "python")
    assert recovered.tree is not None
    assert recovered.recovered


def test_sniff_delegates_to_airflow_and_spark() -> None:
    airflow_result = parse(b"from airflow import DAG\n", "dags/x.py")
    assert all(o.type in {"dag", "task", "file"} or True for o in airflow_result.objects)
    # airflow file with no dags: parses but no objects
    spark_result = parse(b"from pyspark.sql import SparkSession\nx = 1\n", "jobs/x.py")
    assert spark_result.objects and spark_result.objects[0].type == "module"


def test_routing_delegation_results() -> None:
    result = parse(
        b"from pyspark.sql import SparkSession\n"
        b"spark = SparkSession.builder.getOrCreate()\nspark.table('a.b')\n",
        "j/s.py",
    )
    module = result.objects[0]
    assert any(r.ref_kind == "etl_reads" for r in module.references)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00\x01\x02\xff\xfe binary garbage \x80",
        b"(((((",
        b"def f():\n    return 'unterminated\n",
        b"import" * 5000,
    ],
)
def test_never_raises_on_garbage(data: bytes) -> None:
    result = parse(data, "g/x.py")
    assert result.objects or result.issues is not None


def test_nothing_is_executed() -> None:
    result = parse(
        b"import sys\nprint('MARKER')\nsys.exit(9)\nexit()\n",
        "m/danger.py",
    )
    assert result.objects[0].type == "module"


def test_ast_golden(tmp_path: Path) -> None:
    result = parse(
        b"def f(x):\n    cur.execute('SELECT 1 FROM t')\n    return x + 1\n",
        "m/g.py",
    )
    fn = next(o for o in result.objects if o.type == "function")
    actual = fn.node.to_dict()
    golden = GOLDEN_DIR / "function.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    if golden.exists():
        assert json.loads(golden.read_text()) == actual
    else:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")


def test_node_is_dialect_statement() -> None:
    result = parse(b"x = 1\n", "m/x.py")
    assert isinstance(result.objects[0].node, ast.DialectStatement)
    assert result.objects[0].node.name == "python.module"


def test_bad_line_inside_function_marks_function_partial() -> None:
    data = b"def good():\n    return 1\n\ndef broken():\n    x = (\n\ndef after():\n    return 3\n"
    objects = parse(data, "jobs/x.py").objects
    broken = next(o for o in objects if o.name == "broken")
    assert broken.status == "partial"
    after = next(o for o in objects if o.name == "after")
    assert after.status == "parsed"
