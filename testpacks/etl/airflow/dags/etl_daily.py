"""Daily ETL DAG."""

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.providers.snowflake.operators.snowflake import SnowflakeOperator
from airflow.providers.mssql.operators.mssql import MsSqlOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sensors.external_task import ExternalTaskSensor

with DAG("etl_daily", schedule_interval="0 2 * * *", template_searchpath=["sql"]) as dag:
    start = EmptyOperator(task_id="start")
    wait_upstream = ExternalTaskSensor(
        task_id="wait_upstream", external_dag_id="landing", external_task_id="done"
    )
    ext_orders = SnowflakeOperator(
        task_id="ext_orders", sql="INSERT INTO stg.orders SELECT * FROM src.orders"
    )
    ext_customers = SnowflakeOperator(task_id="ext_customers", sql="sql/ext_customers.sql")
    ext_products = BashOperator(task_id="ext_products", bash_command="sh scripts/extract_products.sh")
    stg_done = EmptyOperator(task_id="stg_done")
    load_orders = MsSqlOperator(task_id="load_orders", sql="sql/load_orders.sql")
    load_customers = MsSqlOperator(
        task_id="load_customers", sql="INSERT INTO mart.customers SELECT * FROM stg.customers"
    )
    build_summary = SnowflakeOperator(
        task_id="build_summary",
        sql=["sql/summary.sql", "INSERT INTO mart.daily SELECT CURRENT_DATE"],
    )
    trigger_recon = TriggerDagRunOperator(task_id="trigger_recon", trigger_dag_id="reconcile")
    end = EmptyOperator(task_id="end")

    start >> wait_upstream >> [ext_orders, ext_customers, ext_products] >> stg_done
    stg_done >> [load_orders, load_customers] >> build_summary >> trigger_recon >> end
