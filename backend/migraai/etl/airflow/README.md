# airflow ETL parser

`.py` DAG files (delegated from `python.parse` when an `airflow` import is
seen) and dag-factory `.yml`/`.yaml` files.

## Objects
- `dag` — qualified by dag_id. Detected via `with DAG(...) as d:`,
  `d = DAG(...)`, `models.DAG`/`airflow.DAG`, and `@dag`/`@dag()` decorated
  functions (dag_id = kwarg or function name). Node args carry `dag_id`,
  `schedule_interval` (yaml) and `tasks` (Identifier list).
- `task` — `<dag_id>.<task_id>`. Operator calls with `task_id=` or in the
  known set; `@task`/`@task.<x>` decorated functions; `TaskGroup("g")` prefixes
  ids with `g.`. Unknown operators still produce a task + info
  `airflow.unknown_operator`.

## References
- Dependencies `a >> b` / `a << b` / lists / `set_upstream`/`set_downstream` /
  `chain`/`cross_downstream` → `etl_runs` ref on the *upstream* task to the
  downstream `<dag>.<task>`.
- `TriggerDagRunOperator(trigger_dag_id="x")` → task `etl_runs` `x`.
- `ExternalTaskSensor(external_dag_id, external_task_id)` → `etl_reads`
  `dag.task` (or `dag`).

## SQL & shell
- `sql=`/`hql=`/`query=` kwargs: str ending `.sql`/`.hql` (or list) →
  `etl_runs` path (relative to the DAG file dir; `template_searchpath` first
  entry is resolved relative to pack root); other literals → EmbeddedSql with
  a dialect hint from the operator class (Snowflake/MsSql/Oracle/Hive/
  Teradata → matching; Postgres/SQLExecuteQuery/others → None).
- `bash_command=` → parsed via `shell.parse_command_line`; refs and SQL land
  on the task.

## YAML
dag-factory shape `{dag_id: {…, tasks: {task_id: {operator, dependencies,
sql|bash_command}}}}` → same objects/refs. Non-dag YAML → warning
`airflow.unrecognized_yaml`, no objects.

## Known gaps
- `chain(...)`/`cross_downstream` handling is linear (first/last of each list
  edge).
- TaskFlow `x = extract(); load(x)` dependencies are only detected when the
  callee is a `@task` function in the same DAG (decorated `@dag` funcs) — see
  tests.
