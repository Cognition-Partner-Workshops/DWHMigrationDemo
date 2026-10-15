"""Maintenance DAG: set_upstream/set_downstream and custom operator."""

from airflow.models import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.hive import HiveOperator
from my_vendors.ops import PurgeArchiveOperator

dag = DAG("maintenance", schedule_interval="@weekly")

init = EmptyOperator(task_id="init", dag=dag)
purge_stg = HiveOperator(task_id="purge_stg", hql="TRUNCATE TABLE stg.tmp_load", dag=dag)
vacuum = PurgeArchiveOperator(task_id="vacuum", dag=dag)
reindex = HiveOperator(
    task_id="reindex", hql="MSCK REPAIR TABLE mart.orders", dag=dag
)
stats = HiveOperator(
    task_id="stats", hql="ANALYZE TABLE mart.orders COMPUTE STATISTICS", dag=dag
)
archive = EmptyOperator(task_id="archive", dag=dag)
done = EmptyOperator(task_id="done", dag=dag)

purge_stg.set_upstream(init)
vacuum.set_upstream(purge_stg)
reindex.set_upstream(vacuum)
stats.set_upstream(reindex)
archive.set_upstream(stats)
done.set_upstream(archive)
