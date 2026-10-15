"""ETL output formats per target (conversion contract §4).

Descriptions and skeletons are prompt context for the Devin voters. Vendor sources for every
format shape are in ``migraai.etl.outputs.sources.FORMAT_SOURCES``.
"""

from dataclasses import dataclass
from typing import Literal

Language = Literal["python", "json", "yaml", "sql"]


@dataclass(slots=True, frozen=True)
class OutputFormat:
    id: str
    target: str
    language: Language
    file_suffix: str
    description: str
    skeleton: str


_DATABRICKS_NOTEBOOK = """# Databricks notebook source
# Stage open orders from the staging table.
from pyspark.sql import functions as F

source_df = spark.read.table("main.staging.orders")

# COMMAND ----------

# Publish the cleaned rows to the sales schema.
result_df = source_df.filter(F.col("status") == "OPEN").select(
    "order_id", "customer_id", "amount"
)
result_df.write.mode("overwrite").saveAsTable("main.sales.open_orders")
"""

_DATABRICKS_JOB = """{
  "name": "load_orders",
  "tasks": [
    {
      "task_key": "stage_orders",
      "notebook_task": {
        "notebook_path": "/Workspace/migraai/load_orders/stage_orders",
        "source": "WORKSPACE"
      }
    },
    {
      "task_key": "publish_orders",
      "depends_on": [{"task_key": "stage_orders"}],
      "sql_task": {
        "warehouse_id": "<warehouse-id>",
        "file": {
          "path": "/Workspace/migraai/load_orders/publish_orders.sql",
          "source": "WORKSPACE"
        }
      }
    }
  ],
  "schedule": {
    "quartz_cron_expression": "0 0 2 * * ?",
    "timezone_id": "UTC",
    "pause_status": "PAUSED"
  }
}
"""


def _sql_skeleton(table: str) -> str:
    return f"""INSERT INTO {table} (order_id, customer_id, amount)
SELECT order_id, customer_id, amount
FROM staging.orders
WHERE status = 'OPEN';
"""


_BIGQUERY_SQL = """INSERT INTO `my_project.sales.open_orders` (order_id, customer_id, amount)
SELECT order_id, customer_id, amount
FROM `my_project.staging.orders`
WHERE status = 'OPEN';
"""

_SYNAPSE_SQL = """INSERT INTO [sales].[open_orders] ([order_id], [customer_id], [amount])
SELECT [order_id], [customer_id], [amount]
FROM [staging].[orders]
WHERE [status] = 'OPEN';
"""


def _sql_dag(conn_id: str) -> str:
    return f'''import datetime

from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.sdk import DAG

with DAG(
    dag_id="load_orders",
    start_date=datetime.datetime(2026, 1, 1),
    schedule="0 2 * * *",
    catchup=False,
) as dag:
    stage_orders = SQLExecuteQueryOperator(
        task_id="stage_orders",
        conn_id="{conn_id}",
        sql="sql/stage_orders.sql",
    )
    publish_orders = SQLExecuteQueryOperator(
        task_id="publish_orders",
        conn_id="{conn_id}",
        sql="sql/publish_orders.sql",
    )
    stage_orders >> publish_orders
'''


_CLICKHOUSE_DAG = """import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

with DAG(
    dag_id="load_orders",
    start_date=datetime.datetime(2026, 1, 1),
    schedule="0 2 * * *",
    catchup=False,
) as dag:
    stage_orders = BashOperator(
        task_id="stage_orders",
        bash_command=(
            'clickhouse-client --host "$CLICKHOUSE_HOST" '
            "--queries-file /opt/migraai/sql/stage_orders.sql"
        ),
    )
    publish_orders = BashOperator(
        task_id="publish_orders",
        bash_command=(
            'clickhouse-client --host "$CLICKHOUSE_HOST" '
            "--queries-file /opt/migraai/sql/publish_orders.sql"
        ),
    )
    stage_orders >> publish_orders
"""

_COMPOSER_DAG = '''import datetime

from airflow import models
from airflow.providers.google.cloud.operators.bigquery import BigQueryInsertJobOperator

STAGE_ORDERS_SQL = """
INSERT INTO `my_project.staging.orders_clean` (order_id, customer_id, amount)
SELECT order_id, customer_id, amount FROM `my_project.staging.orders`
"""

PUBLISH_ORDERS_SQL = """
INSERT INTO `my_project.sales.open_orders` (order_id, customer_id, amount)
SELECT order_id, customer_id, amount FROM `my_project.staging.orders_clean`
WHERE status = 'OPEN'
"""

with models.DAG(
    dag_id="load_orders",
    start_date=datetime.datetime(2026, 1, 1),
    schedule="0 2 * * *",
    catchup=False,
) as dag:
    stage_orders = BigQueryInsertJobOperator(
        task_id="stage_orders",
        configuration={"query": {"query": STAGE_ORDERS_SQL, "useLegacySql": False}},
    )
    publish_orders = BigQueryInsertJobOperator(
        task_id="publish_orders",
        configuration={"query": {"query": PUBLISH_ORDERS_SQL, "useLegacySql": False}},
    )
    stage_orders >> publish_orders
'''

_SNOWFLAKE_TASKS = """CREATE OR REPLACE TASK load_orders_root
  WAREHOUSE = transform_wh
  SCHEDULE = 'USING CRON 0 2 * * * UTC'
AS
  CALL staging.load_orders();

CREATE OR REPLACE TASK publish_orders
  WAREHOUSE = transform_wh
  AFTER load_orders_root
AS
  INSERT INTO sales.open_orders (order_id, customer_id, amount)
  SELECT order_id, customer_id, amount FROM staging.orders WHERE status = 'OPEN';
"""

_FABRIC_NOTEBOOK = """# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# CELL ********************

from pyspark.sql import functions as F

source_df = spark.read.table("staging_orders")
result_df = source_df.filter(F.col("status") == "OPEN").select(
    "order_id", "customer_id", "amount"
)
result_df.write.mode("overwrite").saveAsTable("open_orders")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
"""

_FABRIC_PIPELINE = """{
  "name": "load_orders",
  "properties": {
    "activities": [
      {
        "name": "StageOrders",
        "type": "TridentNotebook",
        "dependsOn": [],
        "typeProperties": {
          "notebookId": "<notebook-id>",
          "workspaceId": "<workspace-id>"
        }
      },
      {
        "name": "PublishOrders",
        "type": "TridentNotebook",
        "dependsOn": [
          {"activity": "StageOrders", "dependencyConditions": ["Succeeded"]}
        ],
        "typeProperties": {
          "notebookId": "<notebook-id>",
          "workspaceId": "<workspace-id>"
        }
      }
    ]
  }
}
"""

_SYNAPSE_PIPELINE = """{
  "name": "load_orders",
  "properties": {
    "activities": [
      {
        "name": "StageOrders",
        "type": "SynapseNotebook",
        "dependsOn": [],
        "typeProperties": {
          "notebook": {"referenceName": "stage_orders", "type": "NotebookReference"}
        }
      },
      {
        "name": "PublishOrders",
        "type": "SqlServerStoredProcedure",
        "dependsOn": [
          {"activity": "StageOrders", "dependencyConditions": ["Succeeded"]}
        ],
        "linkedServiceName": {
          "referenceName": "SynapseDedicatedPool",
          "type": "LinkedServiceReference"
        },
        "typeProperties": {"storedProcedureName": "sales.usp_publish_open_orders"}
      }
    ]
  }
}
"""

_SQL_DESC = (
    "{name} SQL script: one or more complete statements in the {name} dialect, each ending "
    "with a semicolon. Use only {name} syntax and functions, fully qualify object names "
    "({qual}), keep string literals and quoted identifiers closed, and keep parentheses "
    "balanced. Orchestration belongs in the companion job format, not in this file."
)

_DAG_DESC = (
    "Apache Airflow DAG file (Python) for {name}. Define exactly one DAG with "
    "`with DAG(dag_id=..., start_date=..., schedule=..., catchup=False) as dag:` (or a "
    "`@dag`-decorated function), import DAG from airflow, create one task per source ETL "
    "step ({operator}), and wire dependencies with `>>`. The file must parse as Python; it is "
    "checked statically and never executed, so keep top-level code to imports, constants and "
    "the DAG definition."
)

_FORMATS: tuple[OutputFormat, ...] = (
    OutputFormat(
        id="databricks.notebook",
        target="databricks",
        language="python",
        file_suffix=".py",
        description=(
            "Databricks PySpark notebook in source format: a `.py` file whose first line is "
            "exactly `# Databricks notebook source`, with cells separated by lines that are "
            "exactly `# COMMAND ----------`. Use the notebook's `spark` session, read and write "
            "Unity Catalog tables by three-part name (catalog.schema.table), and keep each "
            "cell to one logical step of the source job. The file must parse as Python."
        ),
        skeleton=_DATABRICKS_NOTEBOOK,
    ),
    OutputFormat(
        id="databricks.workflows_job",
        target="databricks",
        language="json",
        file_suffix=".job.json",
        description=(
            "Databricks Workflows (Lakeflow Jobs) job definition as a Jobs API create-job JSON "
            "body: a `name` and a `tasks` array. Each task has a unique `task_key` (letters, "
            "digits, `_` or `-`, at most 100 characters), an optional `depends_on` list of "
            '`{"task_key": ...}` entries naming other tasks in the same job, and exactly one '
            "of `notebook_task` (`notebook_path`) or `sql_task` (`warehouse_id` plus `file`, "
            "`query`, `alert` or `dashboard`). Dependencies must form a DAG. An optional "
            "`schedule` needs `quartz_cron_expression` and `timezone_id`; emit "
            '`"pause_status": "PAUSED"` so converted jobs do not start on their own.'
        ),
        skeleton=_DATABRICKS_JOB,
    ),
    OutputFormat(
        id="redshift.sql",
        target="redshift",
        language="sql",
        file_suffix=".sql",
        description=_SQL_DESC.format(name="Amazon Redshift", qual="schema.object"),
        skeleton=_sql_skeleton("sales.open_orders"),
    ),
    OutputFormat(
        id="redshift.airflow_dag",
        target="redshift",
        language="python",
        file_suffix="_dag.py",
        description=_DAG_DESC.format(
            name="Amazon Redshift (for example on Amazon MWAA)",
            operator="SQLExecuteQueryOperator with a Redshift connection id and a .sql file",
        ),
        skeleton=_sql_dag("redshift_default"),
    ),
    OutputFormat(
        id="snowflake.sql",
        target="snowflake",
        language="sql",
        file_suffix=".sql",
        description=_SQL_DESC.format(name="Snowflake", qual="database.schema.object"),
        skeleton=_sql_skeleton("analytics.sales.open_orders"),
    ),
    OutputFormat(
        id="snowflake.tasks_sql",
        target="snowflake",
        language="sql",
        file_suffix=".tasks.sql",
        description=(
            "Snowflake task graph as SQL: one `CREATE [OR REPLACE] TASK <name> ... AS <sql>;` "
            "statement per source ETL step, all in the same schema. The root task has "
            "`SCHEDULE = 'USING CRON ...'` (or an interval); every child task has "
            "`AFTER <predecessor>[, ...]` naming tasks defined in this file and no SCHEDULE. "
            "The body after AS is a single SQL statement, a stored procedure CALL, or a "
            "Snowflake Scripting block. Tasks are created suspended; do not add ALTER TASK ... "
            "RESUME, which is left to deployment."
        ),
        skeleton=_SNOWFLAKE_TASKS,
    ),
    OutputFormat(
        id="snowflake.airflow_dag",
        target="snowflake",
        language="python",
        file_suffix="_dag.py",
        description=_DAG_DESC.format(
            name="Snowflake (use only when the project orchestrates with Airflow instead of "
            "Snowflake Tasks)",
            operator="SQLExecuteQueryOperator with a Snowflake connection id and a .sql file",
        ),
        skeleton=_sql_dag("snowflake_default"),
    ),
    OutputFormat(
        id="fabric.notebook",
        target="fabric",
        language="python",
        file_suffix=".py",
        description=(
            "Microsoft Fabric PySpark notebook in Fabric Git source format "
            "(notebook-content.py): first line exactly `# Fabric notebook source`, a "
            "`# METADATA ********************` block of `# META` lines holding JSON (kernel "
            "info), then one `# CELL ********************` section per step, each followed by "
            "its own `# METADATA` block. Read and write Lakehouse tables with the notebook's "
            "`spark` session. The file must parse as Python."
        ),
        skeleton=_FABRIC_NOTEBOOK,
    ),
    OutputFormat(
        id="fabric.pipeline",
        target="fabric",
        language="json",
        file_suffix=".pipeline.json",
        description=(
            "Microsoft Fabric Data Factory pipeline JSON (pipeline-content.json shape): a "
            "`name` and `properties.activities`. Each activity has a unique `name`, a `type` "
            "(for example `TridentNotebook` for a Fabric notebook), `typeProperties`, and a "
            '`dependsOn` list of `{"activity": <name>, "dependencyConditions": [...]}` '
            "entries using Succeeded, Failed, Skipped or Completed. Dependencies must name "
            "activities in the same pipeline and must not form a cycle."
        ),
        skeleton=_FABRIC_PIPELINE,
    ),
    OutputFormat(
        id="bigquery.sql",
        target="bigquery",
        language="sql",
        file_suffix=".sql",
        description=_SQL_DESC.format(
            name="BigQuery GoogleSQL", qual="`project.dataset.table` in backticks"
        ),
        skeleton=_BIGQUERY_SQL,
    ),
    OutputFormat(
        id="bigquery.composer_dag",
        target="bigquery",
        language="python",
        file_suffix="_dag.py",
        description=_DAG_DESC.format(
            name="Cloud Composer (Managed Airflow) running BigQuery jobs",
            operator="BigQueryInsertJobOperator with a query job configuration and "
            "useLegacySql set to False",
        ),
        skeleton=_COMPOSER_DAG,
    ),
    OutputFormat(
        id="synapse.pipeline",
        target="synapse",
        language="json",
        file_suffix=".pipeline.json",
        description=(
            "Azure Synapse Analytics pipeline JSON: a `name` and `properties.activities`. Each "
            "activity has a unique `name` (at most 55 characters, starting with a letter, "
            "digit or underscore, without . + ? / < > * % & : or spaces), a `type` (for example "
            "`SynapseNotebook` or `SqlServerStoredProcedure`), optional `typeProperties` and "
            '`linkedServiceName`, and a `dependsOn` list of `{"activity": <name>, '
            '"dependencyConditions": [...]}` entries using Succeeded, Failed, Skipped or '
            "Completed. Dependencies must name activities in the same pipeline and must not "
            "form a cycle."
        ),
        skeleton=_SYNAPSE_PIPELINE,
    ),
    OutputFormat(
        id="synapse.sql",
        target="synapse",
        language="sql",
        file_suffix=".sql",
        description=_SQL_DESC.format(
            name="Synapse dedicated SQL pool T-SQL", qual="schema.object, bracket-quoted"
        ),
        skeleton=_SYNAPSE_SQL,
    ),
    OutputFormat(
        id="clickhouse.sql",
        target="clickhouse",
        language="sql",
        file_suffix=".sql",
        description=_SQL_DESC.format(name="ClickHouse", qual="database.table"),
        skeleton=_sql_skeleton("sales.open_orders"),
    ),
    OutputFormat(
        id="clickhouse.airflow_dag",
        target="clickhouse",
        language="python",
        file_suffix="_dag.py",
        description=_DAG_DESC.format(
            name="ClickHouse",
            operator="BashOperator running `clickhouse-client --queries-file <file.sql>`",
        ),
        skeleton=_CLICKHOUSE_DAG,
    ),
)

FORMATS: dict[str, OutputFormat] = {fmt.id: fmt for fmt in _FORMATS}
TARGETS: tuple[str, ...] = (
    "databricks",
    "redshift",
    "snowflake",
    "fabric",
    "bigquery",
    "synapse",
    "clickhouse",
)


def formats_for(target: str) -> list[OutputFormat]:
    if target not in TARGETS:
        raise ValueError(f"unknown target: {target!r}")
    return [fmt for fmt in _FORMATS if fmt.target == target]


def get_format(format_id: str) -> OutputFormat:
    try:
        return FORMATS[format_id]
    except KeyError:
        raise ValueError(f"unknown ETL output format: {format_id!r}") from None
