"""Cleanup DAG — chain() and list dependencies."""

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.providers.teradata.operators.teradata import TeradataOperator
from airflow.utils.helpers import chain

with DAG("cleanup", schedule_interval="@daily") as dag:
    pre = EmptyOperator(task_id="pre")
    drop_tmp = TeradataOperator(task_id="drop_tmp", sql="DROP TABLE stg.tmp_a")
    drop_old = TeradataOperator(task_id="drop_old", sql="DROP TABLE stg.tmp_b")
    purge_logs = BashOperator(task_id="purge_logs", bash_command="sh scripts/purge.sh")
    vacuum = EmptyOperator(task_id="vacuum")
    post = EmptyOperator(task_id="post")

    chain(pre, [drop_tmp, drop_old], purge_logs, vacuum, post)
