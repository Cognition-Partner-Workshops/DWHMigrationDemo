"""Public ETL output validation entry point."""

from __future__ import annotations

from migraai.etl.outputs.formats import get_format
from migraai.etl.outputs.pipelines import check_json_output
from migraai.etl.outputs.python_checks import (
    check_airflow_dag,
    check_databricks_notebook,
    check_fabric_notebook,
)
from migraai.etl.outputs.sql_checks import check_sql_output
from migraai.validation.findings import Finding


def validate_output(format_id: str, content: str) -> list[Finding]:
    output_format = get_format(format_id)
    if output_format.language == "python":
        if format_id == "databricks.notebook":
            findings = check_databricks_notebook(format_id, content)
        elif format_id == "fabric.notebook":
            findings = check_fabric_notebook(format_id, content)
        else:
            findings = check_airflow_dag(format_id, content)
    elif output_format.language == "json":
        findings = check_json_output(format_id, content)
    elif output_format.language == "sql":
        findings = check_sql_output(format_id, content)
    else:
        findings = []
    findings.sort(key=lambda item: (item.line is None, item.line if item.line is not None else 0))
    return findings
