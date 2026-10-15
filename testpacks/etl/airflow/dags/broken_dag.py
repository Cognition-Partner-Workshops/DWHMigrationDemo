"""A DAG file with a syntax error mid-file; valid parts still parse."""

from airflow import DAG
from airflow.operators.empty import EmptyOperator

with DAG("broken_dag") as dag:
    first = EmptyOperator(task_id="first")
    second = EmptyOperator(task_id="second")
    first >> second


def malformed(:
    pass


with DAG("broken_dag2") as dag2:
    third = EmptyOperator(task_id="third", dag=dag2)
