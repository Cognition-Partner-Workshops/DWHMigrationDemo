"""Reporting DAG using TaskGroup and @task."""

from airflow import DAG
from airflow.decorators import task, dag
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.providers.oracle.operators.oracle import OracleOperator
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.utils.task_group import TaskGroup


@dag(dag_id="reporting", schedule="@daily")
def reporting_dag():
    extract = PostgresOperator(task_id="extract", sql="SELECT * FROM mart.orders")

    with TaskGroup("agg") as agg:
        by_region = OracleOperator(
            task_id="by_region", sql="INSERT INTO rpt.region SELECT region, COUNT(*) FROM mart.orders GROUP BY region"
        )
        by_product = OracleOperator(
            task_id="by_product", sql="INSERT INTO rpt.product SELECT prod_id, SUM(amount) FROM mart.orders GROUP BY prod_id"
        )
        by_region >> by_product

    @task
    def publish():
        return "ok"

    @task(task_id="notify")
    def send_report():
        return None

    done = BashOperator(task_id="done", bash_command="echo done")
    extract >> agg >> publish() >> send_report() >> done


reporting_dag()
