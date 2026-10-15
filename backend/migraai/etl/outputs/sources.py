"""Vendor documentation behind each ETL output format's shape (KB claim format)."""

_ACCESSED = "2026-10-15"

_DBX_NOTEBOOK_SOURCE = "https://docs.databricks.com/aws/en/notebooks/notebook-export-import"
_DBX_JOBS_CREATE = "https://docs.databricks.com/api/workspace/jobs/create"
_AIRFLOW_DAGS = "https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html"
_AIRFLOW_SQL_OPERATOR = (
    "https://airflow.apache.org/docs/apache-airflow-providers-common-sql/stable/operators.html"
)
_AIRFLOW_REDSHIFT_SQL = (
    "https://airflow.apache.org/docs/apache-airflow-providers-amazon/stable/operators/"
    "redshift/redshift_sql.html"
)
_AIRFLOW_SNOWFLAKE_SQL = (
    "https://airflow.apache.org/docs/apache-airflow-providers-snowflake/stable/operators/"
    "snowflake.html"
)
_AIRFLOW_BIGQUERY = (
    "https://airflow.apache.org/docs/apache-airflow-providers-google/stable/operators/cloud/"
    "bigquery.html"
)
_AIRFLOW_BASH = (
    "https://airflow.apache.org/docs/apache-airflow-providers-standard/stable/operators/bash.html"
)
_MWAA = "https://docs.aws.amazon.com/mwaa/latest/userguide/what-is-mwaa.html"
_COMPOSER_DAGS = "https://docs.cloud.google.com/composer/docs/composer-3/write-dags"
_SNOWFLAKE_CREATE_TASK = "https://docs.snowflake.com/en/sql-reference/sql/create-task"
_SNOWFLAKE_TASK_GRAPHS = "https://docs.snowflake.com/en/user-guide/tasks-graphs"
_SNOWFLAKE_SQL = "https://docs.snowflake.com/en/sql-reference/sql-all"
_ADF_PIPELINES = (
    "https://learn.microsoft.com/en-us/azure/data-factory/concepts-pipelines-activities"
)
_ADF_STORED_PROCEDURE = (
    "https://learn.microsoft.com/en-us/azure/data-factory/transform-data-using-stored-procedure"
)
_SYNAPSE_NOTEBOOK_ACTIVITY = (
    "https://learn.microsoft.com/en-us/azure/synapse-analytics/synapse-notebook-activity"
)
_SYNAPSE_SQL = "https://learn.microsoft.com/en-us/azure/synapse-analytics/sql/overview-features"
_FABRIC_PIPELINE_DEFINITION = (
    "https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/"
    "datapipeline-definition"
)
_FABRIC_PIPELINE_REST = (
    "https://learn.microsoft.com/en-us/fabric/data-factory/pipeline-rest-api-capabilities"
)
_FABRIC_NOTEBOOK_DEFINITION = (
    "https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/"
    "notebook-definition"
)
_FABRIC_NOTEBOOK_GIT = (
    "https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-source-control-deployment"
)
_REDSHIFT_SQL = "https://docs.aws.amazon.com/redshift/latest/dg/c_SQL_commands.html"
_CLICKHOUSE_SQL = "https://clickhouse.com/docs/reference/statements"
_CLICKHOUSE_CLIENT = "https://clickhouse.com/docs/concepts/features/interfaces/client"
_BIGQUERY_SQL = "https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/query-syntax"


def _claim(text: str, url: str, confidence: str = "high") -> dict[str, str]:
    return {"text": text, "url": url, "accessed": _ACCESSED, "confidence": confidence}


_AIRFLOW_DAG_CLAIMS = (
    _claim(
        "An Airflow DAG is declared with a `with DAG(dag_id=..., start_date=..., schedule=...)` "
        "context manager, a standard `DAG(...)` constructor passed to operators, or the `@dag` "
        "decorator.",
        _AIRFLOW_DAGS,
    ),
    _claim(
        "Task dependencies in an Airflow DAG are declared with the `>>` and `<<` operators.",
        _AIRFLOW_DAGS,
    ),
)

FORMAT_SOURCES: dict[str, tuple[dict[str, str], ...]] = {
    "databricks.notebook": (
        _claim(
            "A Databricks Python notebook source file starts with the comment "
            "`# Databricks notebook source`.",
            _DBX_NOTEBOOK_SOURCE,
        ),
        _claim(
            "Cells in a Databricks Python notebook source file are separated by the special "
            "comment `# COMMAND ----------`.",
            _DBX_NOTEBOOK_SOURCE,
        ),
    ),
    "databricks.workflows_job": (
        _claim(
            "A Jobs API create-job request has a `tasks` array; each task's `task_key` is "
            "required, unique within the job, 1-100 characters matching `^[\\w\\-\\_]+$`.",
            _DBX_JOBS_CREATE,
        ),
        _claim(
            "A task's optional `depends_on` array lists `{task_key}` objects of the tasks that "
            "must complete before it runs.",
            _DBX_JOBS_CREATE,
        ),
        _claim(
            "A task runs a notebook when `notebook_task` (with required `notebook_path`) is "
            "present, and runs SQL when `sql_task` is present with `warehouse_id` and one of "
            "`query`, `file`, `alert` or `dashboard`.",
            _DBX_JOBS_CREATE,
        ),
        _claim(
            "A job `schedule` requires `quartz_cron_expression` and `timezone_id`.",
            _DBX_JOBS_CREATE,
        ),
    ),
    "redshift.sql": (_claim("Amazon Redshift SQL command reference.", _REDSHIFT_SQL),),
    "redshift.airflow_dag": (
        *_AIRFLOW_DAG_CLAIMS,
        _claim(
            "Airflow runs SQL against Amazon Redshift with SQLExecuteQueryOperator.",
            _AIRFLOW_REDSHIFT_SQL,
        ),
        _claim("Amazon MWAA is a managed service for Apache Airflow on AWS.", _MWAA),
    ),
    "snowflake.sql": (_claim("Snowflake SQL command reference (all commands).", _SNOWFLAKE_SQL),),
    "snowflake.tasks_sql": (
        _claim(
            "CREATE TASK takes options such as WAREHOUSE, SCHEDULE and AFTER, followed by "
            "`AS <sql>`, where the body is a single SQL statement, a stored procedure call or "
            "Snowflake Scripting.",
            _SNOWFLAKE_CREATE_TASK,
        ),
        _claim(
            "AFTER names one or more predecessor tasks; a schedule can be defined only for a "
            "standalone or root task, not for child tasks.",
            _SNOWFLAKE_CREATE_TASK,
        ),
        _claim("Newly created tasks are created suspended.", _SNOWFLAKE_CREATE_TASK),
        _claim(
            "A task graph starts with a root task and links child tasks by dependencies.",
            _SNOWFLAKE_TASK_GRAPHS,
        ),
    ),
    "snowflake.airflow_dag": (
        *_AIRFLOW_DAG_CLAIMS,
        _claim(
            "Airflow runs SQL against Snowflake with SQLExecuteQueryOperator.",
            _AIRFLOW_SNOWFLAKE_SQL,
        ),
    ),
    "fabric.notebook": (
        _claim(
            "Fabric stores a PySpark notebook in Git as notebook-content.py, with metadata, "
            "markdown cells and code cells as separate sections.",
            _FABRIC_NOTEBOOK_GIT,
        ),
        _claim(
            "The Fabric Git source format starts with `# Fabric notebook source` and uses "
            "`# METADATA ********************` blocks of `# META` JSON lines and "
            "`# CELL ********************` markers.",
            _FABRIC_NOTEBOOK_DEFINITION,
        ),
    ),
    "fabric.pipeline": (
        _claim(
            "A Fabric pipeline-content.json holds `properties.activities`; each activity "
            "requires `name`, `type` and `typeProperties` and may have `dependsOn`, whose "
            "entries require `activity` and `dependencyConditions`.",
            _FABRIC_PIPELINE_DEFINITION,
        ),
        _claim(
            "`TridentNotebook` activities take `notebookId` and `workspaceId` in `typeProperties`.",
            _FABRIC_PIPELINE_DEFINITION,
        ),
        _claim(
            "The Fabric REST API example pipeline JSON has a top-level `name` and "
            "`properties.activities`.",
            _FABRIC_PIPELINE_REST,
        ),
    ),
    "bigquery.sql": (_claim("BigQuery GoogleSQL query syntax reference.", _BIGQUERY_SQL),),
    "bigquery.composer_dag": (
        _claim(
            "Cloud Composer runs Airflow DAGs written in Python, for example a "
            "`with models.DAG(...) as dag:` block after `from airflow import models`.",
            _COMPOSER_DAGS,
        ),
        *_AIRFLOW_DAG_CLAIMS,
        _claim(
            "BigQueryInsertJobOperator runs a BigQuery query from a `configuration` with a "
            "`query` job and `useLegacySql: False`.",
            _AIRFLOW_BIGQUERY,
        ),
    ),
    "synapse.pipeline": (
        _claim(
            "A Synapse or Data Factory pipeline JSON has a required `name` and "
            "`properties.activities`; activity names are required, at most 55 characters, "
            "start with a letter, number or underscore and exclude . + ? / < > * % & : and "
            "spaces.",
            _ADF_PIPELINES,
        ),
        _claim(
            "Activity `dependsOn` entries name an upstream `activity` with "
            "`dependencyConditions` of Succeeded, Failed, Skipped or Completed.",
            _ADF_PIPELINES,
        ),
        _claim(
            "A Synapse notebook activity has type `SynapseNotebook` with a `NotebookReference` "
            "in `typeProperties.notebook`.",
            _SYNAPSE_NOTEBOOK_ACTIVITY,
        ),
        _claim(
            "A stored procedure activity has type `SqlServerStoredProcedure`, a "
            "`linkedServiceName` (Azure SQL Database, Azure Synapse Analytics or SQL Server) "
            "and `typeProperties.storedProcedureName`.",
            _ADF_STORED_PROCEDURE,
        ),
    ),
    "synapse.sql": (_claim("T-SQL features supported in Synapse SQL pools.", _SYNAPSE_SQL),),
    "clickhouse.sql": (_claim("ClickHouse SQL statement reference.", _CLICKHOUSE_SQL),),
    "clickhouse.airflow_dag": (
        *_AIRFLOW_DAG_CLAIMS,
        _claim("BashOperator runs a shell command given in `bash_command`.", _AIRFLOW_BASH),
        _claim(
            "clickhouse-client `--queries-file` runs the queries in a file.", _CLICKHOUSE_CLIENT
        ),
    ),
}
