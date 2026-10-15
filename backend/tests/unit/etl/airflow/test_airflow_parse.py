from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.etl.airflow.parse import parse

GOLDEN_DIR = Path(__file__).parent / "golden"


def names(obj):
    return [(r.ref_kind, ".".join(p.value for p in r.name.parts)) for r in obj.references]


def _py(text: str):
    return parse(text.encode(), "dags/pipe.py")


def test_with_dag_tasks_and_deps() -> None:
    result = _py(
        """from airflow import DAG
from airflow.operators.bash import BashOperator
with DAG("orders_daily", schedule_interval="@daily") as dag:
    extract = BashOperator(task_id="extract", bash_command="sh ex.sh")
    load = BashOperator(task_id="load", bash_command="sh ld.sh")
    publish = BashOperator(task_id="publish", bash_command="sh pub.sh")
    extract >> load >> publish
"""
    )
    by = {o.qualified_name: o for o in result.objects}
    assert by["orders_daily"].type == "dag"
    assert {t.value for t in by["orders_daily"].node.args["tasks"]} == {
        "extract",
        "load",
        "publish",
    }
    assert ("runs_after", "orders_daily.extract") in names(by["orders_daily.load"])
    assert ("runs_after", "orders_daily.load") in names(by["orders_daily.publish"])


def test_dag_assignment_and_dag_kwarg() -> None:
    result = _py(
        """from airflow import DAG
from airflow.operators.empty import EmptyOperator
d = DAG("assigned_dag")
a = EmptyOperator(task_id="one", dag=d)
b = EmptyOperator(task_id="two", dag=d)
a << b
"""
    )
    by = {o.qualified_name: o for o in result.objects}
    assert by["assigned_dag"].type == "dag"
    assert ("runs_after", "assigned_dag.two") in names(by["assigned_dag.one"])


def test_decorated_dag_and_task() -> None:
    result = _py(
        """from airflow.decorators import dag, task
@dag(schedule="@daily")
def my_flow():
    @task
    def extract():
        return 1
    @task(task_id="renamed")
    def load(x):
        return x
    data = extract()
    load(data)
my_flow()
"""
    )
    by = {o.qualified_name: o for o in result.objects}
    assert "my_flow" in by
    assert "my_flow.extract" in by
    assert "my_flow.renamed" in by
    # TaskFlow passing: extract upstream of load
    assert ("runs_after", "my_flow.extract") in names(by["my_flow.renamed"])


def test_taskgroup_prefix() -> None:
    result = _py(
        """from airflow import DAG
from airflow.utils.task_group import TaskGroup
from airflow.operators.empty import EmptyOperator
with DAG("grp") as dag:
    with TaskGroup("staging"):
        a = EmptyOperator(task_id="t1")
        b = EmptyOperator(task_id="t2")
        a >> b
"""
    )
    by = {o.qualified_name: o for o in result.objects}
    assert "grp.staging.t1" in by and "grp.staging.t2" in by
    assert ("runs_after", "grp.staging.t1") in names(by["grp.staging.t2"])


def test_sql_kwargs_hints_and_paths() -> None:
    result = _py(
        """from airflow import DAG
from airflow.providers.snowflake.operators.snowflake import SnowflakeOperator
from airflow.providers.mssql.operators.mssql import MsSqlOperator
with DAG("sqldag", template_searchpath=["sql"]) as dag:
    a = SnowflakeOperator(task_id="sf", sql="SELECT 1 FROM x")
    b = MsSqlOperator(task_id="ms", sql=["t1.sql", "SELECT 2"])
"""
    )
    by = {o.qualified_name: o for o in result.objects}
    hints = {e.owner_qualified_name: e.dialect_hint for e in result.embedded_sql}
    assert hints["sqldag.sf"] == "snowflake"
    assert hints["sqldag.ms"] == "sqlserver"
    assert ("etl_runs", "sql/t1.sql") in names(by["sqldag.ms"])


def test_trigger_and_sensor() -> None:
    result = _py(
        """from airflow import DAG
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sensors.external_task import ExternalTaskSensor
with DAG("coord") as dag:
    t = TriggerDagRunOperator(task_id="kick", trigger_dag_id="down_dag")
    s = ExternalTaskSensor(task_id="wait", external_dag_id="up_dag", external_task_id="prep")
"""
    )
    by = {o.qualified_name: o for o in result.objects}
    assert ("etl_runs", "down_dag") in names(by["coord.kick"])
    assert ("etl_reads", "up_dag.prep") in names(by["coord.wait"])


def test_set_upstream_and_chain() -> None:
    result = _py(
        """from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.utils.helpers import chain
with DAG("ch") as dag:
    a = EmptyOperator(task_id="a")
    b = EmptyOperator(task_id="b")
    c = EmptyOperator(task_id="c")
    b.set_upstream(a)
    chain(b, c)
"""
    )
    by = {o.qualified_name: o for o in result.objects}
    assert ("runs_after", "ch.a") in names(by["ch.b"])
    assert ("runs_after", "ch.b") in names(by["ch.c"])


def test_unknown_operator_still_task() -> None:
    result = _py(
        """from airflow import DAG
from vendors.ops import QuantumETLOperator
with DAG("u") as dag:
    q = QuantumETLOperator(task_id="q1", mode="fast")
"""
    )
    by = {o.qualified_name: o for o in result.objects}
    assert "u.q1" in by
    assert any(i.code == "airflow.unknown_operator" for i in result.issues)


def test_bash_command_invocations() -> None:
    result = _py(
        """from airflow import DAG
from airflow.operators.bash import BashOperator
with DAG("b") as dag:
    t = BashOperator(task_id="t", bash_command="sqlplus u/p@d @load.sql && sh go.sh")
"""
    )
    task = next(o for o in result.objects if o.type == "task")
    assert ("etl_runs", "dags/load.sql") in names(task)
    assert ("etl_runs", "dags/go.sh") in names(task)


def test_yaml_dag_factory() -> None:
    result = parse(
        b"""orders:
  schedule_interval: "0 2 * * *"
  tasks:
    extract:
      operator: airflow.operators.bash.BashOperator
      bash_command: sh extract.sh
    load:
      operator: airflow.operators.empty.EmptyOperator
      dependencies: [extract]
    publish:
      operator: CustomETLOperator
      dependencies: [load]
""",
        "dags/factory.yaml",
    )
    by = {o.qualified_name: o for o in result.objects}
    assert by["orders"].type == "dag"
    assert ("runs_after", "orders.extract") in names(by["orders.load"])
    assert ("runs_after", "orders.load") in names(by["orders.publish"])
    assert ("etl_runs", "dags/extract.sh") in names(by["orders.extract"])


def test_yaml_unrecognized() -> None:
    result = parse(b"key: value\nlist: [1,2]\n", "cfg.yaml")
    assert result.objects == []
    assert any(i.code == "airflow.unrecognized_yaml" for i in result.issues)


def test_yaml_malformed() -> None:
    result = parse(b"a: [unclosed\nb: : :\n", "bad.yaml")
    assert result.objects[0].type == "file" or result.issues


@pytest.mark.parametrize(
    "data",
    [b"", b"\xff\xfe\x00 garbage", b"def f(:\n", b"{{{{", b"x" * 50000],
)
def test_never_raises(data: bytes) -> None:
    assert parse(data, "g/x.py") is not None
    assert parse(data, "g/x.yaml") is not None


def test_secret_in_bash_redacted() -> None:
    result = _py(
        """from airflow import DAG
from airflow.operators.bash import BashOperator
with DAG("s") as dag:
    t = BashOperator(task_id="t", bash_command="sqlplus u/pw9@db @x.sql")
"""
    )
    payload = json.dumps([o.to_dict() for o in result.objects])
    assert "pw9" not in payload


def test_ast_golden() -> None:
    result = _py(
        """from airflow import DAG
from airflow.operators.bash import BashOperator
with DAG("g") as dag:
    a = BashOperator(task_id="a", bash_command="echo hi")
    b = BashOperator(task_id="b", bash_command="echo bye")
    a >> b
"""
    )
    dag = next(o for o in result.objects if o.type == "dag")
    actual = dag.node.to_dict()
    golden = GOLDEN_DIR / "dag.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1" or not golden.exists():
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert json.loads(golden.read_text()) == actual


def test_broken_python_partial_matches_python_parse() -> None:
    from migraai.etl.python.parse import parse as python_parse

    data = (
        b"from airflow import DAG\n"
        b"from airflow.operators.empty import EmptyOperator\n\n"
        b'with DAG("broken_dag") as dag:\n'
        b'    first = EmptyOperator(task_id="first")\n'
        b'    second = EmptyOperator(task_id="second")\n'
        b"    first >> second\n\n\n"
        b"def malformed(:\n"
        b"    pass\n"
    )
    airflow_result = parse(data, "dags/broken_dag.py")
    python_result = python_parse(data, "dags/broken_dag.py")
    af_mod = next(o for o in airflow_result.objects if o.type == "module")
    py_mod = next(o for o in python_result.objects if o.type == "module")
    assert af_mod.qualified_name == py_mod.qualified_name == "dags.broken_dag"
    assert af_mod.status == py_mod.status == "partial"
    dag = next(o for o in airflow_result.objects if o.type == "dag")
    assert dag.status == "parsed"


def test_broken_dag_pack_partial() -> None:
    data = (Path("../testpacks/etl/airflow") / "dags" / "broken_dag.py").read_bytes()
    objects = parse(data, "dags/broken_dag.py").objects
    mod = next(o for o in objects if o.type == "module")
    assert mod.qualified_name == "dags.broken_dag"
    assert mod.status == "partial"
