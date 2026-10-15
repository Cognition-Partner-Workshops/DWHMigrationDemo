"""AST goldens for representative airflow constructs (§10)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.etl.airflow.parse import parse

GOLDEN_DIR = Path(__file__).parent / "golden"

CASES = {
    "with_dag": (
        b"from airflow import DAG\nfrom airflow.operators.empty import EmptyOperator\n"
        b"with DAG('g') as d:\n    a = EmptyOperator(task_id='a')\n",
        "dags/g.py",
        "dag",
    ),
    "taskflow": (
        b"from airflow.decorators import task, dag\n"
        b"@dag(dag_id='tf')\ndef pipe():\n    @task\n    def ext():\n        return 1\n    @task\n    def load(x):\n        pass\n    load(ext())\npipe()\n",
        "dags/tf.py",
        "task",
    ),
    "taskgroup_deps": (
        b"from airflow import DAG\nfrom airflow.operators.empty import EmptyOperator\n"
        b"from airflow.utils.task_group import TaskGroup\n"
        b"with DAG('tg') as d:\n    a = EmptyOperator(task_id='a')\n"
        b"    with TaskGroup('g') as g:\n        b = EmptyOperator(task_id='b')\n"
        b"    a >> g\n",
        "dags/tg.py",
        "dag",
    ),
    "trigger_dagrun": (
        b"from airflow import DAG\nfrom airflow.operators.trigger_dagrun import TriggerDagRunOperator\n"
        b"with DAG('src') as d:\n    t = TriggerDagRunOperator(task_id='t', trigger_dag_id='child')\n",
        "dags/s.py",
        "task",
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
