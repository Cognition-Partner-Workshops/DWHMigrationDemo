from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from migraai.etl.outputs import (
    FORMAT_SOURCES,
    FORMATS,
    OutputFormat,
    formats_for,
    get_format,
    validate_output,
)

EXPECTED_FORMATS = {
    "databricks": ["databricks.notebook", "databricks.workflows_job"],
    "redshift": ["redshift.sql", "redshift.airflow_dag"],
    "snowflake": ["snowflake.sql", "snowflake.tasks_sql", "snowflake.airflow_dag"],
    "fabric": ["fabric.notebook", "fabric.pipeline"],
    "bigquery": ["bigquery.sql", "bigquery.composer_dag"],
    "synapse": ["synapse.pipeline", "synapse.sql"],
    "clickhouse": ["clickhouse.sql", "clickhouse.airflow_dag"],
}

INVALID_SAMPLES = {
    "databricks.notebook": [
        ("parse error", "# Databricks notebook source\nvalue =\n", {"parse_error"}),
        (
            "bad header and separator",
            "value = 1\n# COMMAND broken\n",
            {"missing_header", "malformed_cell_separator"},
        ),
        ("no statements", "# Databricks notebook source\n# COMMAND ----------\n", {"empty"}),
    ],
    "databricks.workflows_job": [
        ("invalid json", "{\n", {"invalid_json"}),
        ("schema errors", '{"name": "", "tasks": []}', {"schema"}),
        ("non-object", "[1, 2]", {"schema"}),
        (
            "invalid SQL task source",
            '{"name": "job", "tasks": [{"task_key": "a", "sql_task": {"warehouse_id": "w"}}]}',
            {"schema"},
        ),
        (
            "task and graph rules",
            json.dumps(
                {
                    "name": "job",
                    "tasks": [
                        {
                            "task_key": "A",
                            "depends_on": [{"task_key": "B"}],
                            "notebook_task": {"notebook_path": "/workspace/a"},
                        },
                        {
                            "task_key": "B",
                            "depends_on": [{"task_key": "A"}],
                            "sql_task": {"warehouse_id": "w", "query": {}},
                        },
                        {"task_key": "A", "spark_python_task": {}},
                        {"task_key": "U", "depends_on": [{"task_key": "missing"}]},
                    ],
                }
            ),
            {
                "task_type",
                "unsupported_task_type",
                "duplicate_task_key",
                "unknown_dependency",
                "dependency_cycle",
            },
        ),
    ],
    "redshift.sql": [
        ("comments only", "-- nothing here\n/* still nothing */", {"empty"}),
        ("unclosed comment", "SELECT 1; /* unfinished", {"unterminated_comment"}),
        ("unclosed string", "SELECT 'unfinished", {"unterminated_string"}),
        ("extra close", "SELECT 1);", {"unbalanced_parentheses"}),
    ],
    "redshift.airflow_dag": [
        ("parse error", "from airflow import DAG\nif:\n", {"parse_error"}),
        ("no import or dag", "value = 1\n", {"missing_airflow_import", "missing_dag"}),
        ("missing dag id", "from airflow import DAG\nDAG()\n", {"missing_dag_id"}),
    ],
    "snowflake.sql": [
        ("comments only", "-- empty\n/* also empty */", {"empty"}),
        ("unclosed string", "SELECT 'unfinished", {"unterminated_string"}),
        ("unclosed parenthesis", "SELECT (1", {"unbalanced_parentheses"}),
    ],
    "snowflake.tasks_sql": [
        ("no task", "SELECT 1;", {"no_task"}),
        ("missing body", "CREATE TASK root SCHEDULE = 'daily';", {"missing_body"}),
        (
            "graph rules",
            "CREATE TASK root SCHEDULE = 'daily' AFTER child AS SELECT 1;\n"
            "CREATE TASK child AFTER root AS SELECT 2;\n"
            "CREATE TASK child AFTER missing AS SELECT 3;",
            {
                "duplicate_task",
                "unknown_predecessor",
                "schedule_on_child",
                "dependency_cycle",
            },
        ),
    ],
    "snowflake.airflow_dag": [
        ("parse error", "from airflow import DAG\nif:\n", {"parse_error"}),
        ("no import or dag", "value = 1\n", {"missing_airflow_import", "missing_dag"}),
        ("missing dag id", "from airflow import DAG\nDAG()\n", {"missing_dag_id"}),
    ],
    "fabric.notebook": [
        ("parse error", "# Fabric notebook source\nvalue =\n", {"parse_error"}),
        ("no statements", "# Fabric notebook source\n# CELL ********************\n", {"empty"}),
        (
            "metadata and markers",
            "value = 1\n# METADATA ********************\n# META not-json\n# CELL broken\n",
            {"missing_header", "missing_cell", "invalid_metadata", "malformed_marker"},
        ),
    ],
    "fabric.pipeline": [
        ("invalid json", "{", {"invalid_json"}),
        ("schema errors", '{"name": "pipe", "properties": {"activities": []}}', {"schema"}),
        (
            "graph errors",
            json.dumps(
                {
                    "name": "pipe",
                    "properties": {
                        "activities": [
                            {
                                "name": "A",
                                "type": "Copy",
                                "typeProperties": {},
                                "dependsOn": [
                                    {"activity": "B", "dependencyConditions": ["Succeeded"]}
                                ],
                            },
                            {
                                "name": "B",
                                "type": "Copy",
                                "typeProperties": {},
                                "dependsOn": [
                                    {"activity": "A", "dependencyConditions": ["Succeeded"]},
                                    {"activity": "missing", "dependencyConditions": ["Failed"]},
                                ],
                            },
                            {"name": "A", "type": "Copy", "typeProperties": {}},
                        ]
                    },
                }
            ),
            {"duplicate_activity_name", "unknown_dependency", "dependency_cycle"},
        ),
    ],
    "bigquery.sql": [
        ("comments only", "-- empty", {"empty"}),
        ("unclosed string", "SELECT 'unfinished", {"unterminated_string"}),
        ("extra close", "SELECT 1);", {"unbalanced_parentheses"}),
    ],
    "bigquery.composer_dag": [
        ("parse error", "from airflow import DAG\nif:\n", {"parse_error"}),
        ("no import or dag", "value = 1\n", {"missing_airflow_import", "missing_dag"}),
        ("missing dag id", "from airflow import DAG\nDAG()\n", {"missing_dag_id"}),
    ],
    "synapse.pipeline": [
        ("invalid json", "{", {"invalid_json"}),
        ("schema errors", '{"name": "pipe", "properties": {"activities": []}}', {"schema"}),
        (
            "graph errors",
            json.dumps(
                {
                    "name": "pipe",
                    "properties": {
                        "activities": [
                            {
                                "name": "A",
                                "type": "Copy",
                                "dependsOn": [
                                    {"activity": "B", "dependencyConditions": ["Succeeded"]}
                                ],
                            },
                            {
                                "name": "B",
                                "type": "Copy",
                                "dependsOn": [
                                    {"activity": "A", "dependencyConditions": ["Succeeded"]},
                                    {"activity": "missing", "dependencyConditions": ["Skipped"]},
                                ],
                            },
                            {"name": "A", "type": "Copy"},
                        ]
                    },
                }
            ),
            {"duplicate_activity_name", "unknown_dependency", "dependency_cycle"},
        ),
        (
            "invalid names",
            json.dumps(
                {
                    "name": "bad pipeline",
                    "properties": {"activities": [{"name": "bad.activity", "type": "Copy"}]},
                }
            ),
            {"invalid_pipeline_name", "invalid_activity_name"},
        ),
    ],
    "synapse.sql": [
        ("comments only", "-- empty", {"empty"}),
        ("unclosed string", "SELECT 'unfinished", {"unterminated_string"}),
        ("unclosed bracket identifier", "SELECT [unfinished", {"unterminated_identifier"}),
    ],
    "clickhouse.sql": [
        ("comments only", "-- empty", {"empty"}),
        ("unclosed string", "SELECT 'unfinished", {"unterminated_string"}),
        ("extra close", "SELECT 1);", {"unbalanced_parentheses"}),
    ],
    "clickhouse.airflow_dag": [
        ("parse error", "from airflow import DAG\nif:\n", {"parse_error"}),
        ("no import or dag", "value = 1\n", {"missing_airflow_import", "missing_dag"}),
        ("missing dag id", "from airflow import DAG\nDAG()\n", {"missing_dag_id"}),
    ],
}


def test_registry_returns_expected_formats_and_rejects_unknown_ids() -> None:
    assert {target: [fmt.id for fmt in formats_for(target)] for target in EXPECTED_FORMATS} == (
        EXPECTED_FORMATS
    )
    for fmt in FORMATS.values():
        assert isinstance(fmt, OutputFormat)
        assert fmt.id.startswith(f"{fmt.target}.")
        assert fmt.id.split(".", 1)[1]
        assert fmt.description.strip()
        assert fmt.skeleton.strip()
        assert fmt.language == (
            "python"
            if fmt.id.endswith(("notebook", "airflow_dag", "composer_dag"))
            else "json"
            if fmt.id.endswith(("workflows_job", "pipeline"))
            else "sql"
        )
        assert get_format(fmt.id) is fmt
    with pytest.raises(ValueError):
        formats_for("unknown")
    with pytest.raises(ValueError):
        get_format("unknown.format")


def test_vendor_sources_exist_and_have_valid_metadata() -> None:
    assert set(FORMAT_SOURCES) == set(FORMATS)
    for _format_id, claims in FORMAT_SOURCES.items():
        assert claims
        for claim in claims:
            assert claim["text"].strip()
            assert claim["url"].startswith("https://")
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", claim["accessed"])
            assert claim["confidence"] in {"high", "medium", "low"}
            assert urlparse(claim["url"]).hostname != "example.com"


@pytest.mark.parametrize("format_id", list(FORMATS))
def test_each_format_skeleton_validates_cleanly(format_id: str) -> None:
    assert validate_output(format_id, get_format(format_id).skeleton) == []


@pytest.mark.parametrize(
    ("format_id", "case", "content", "expected"),
    [
        (format_id, case, content, expected)
        for format_id, samples in INVALID_SAMPLES.items()
        for case, content, expected in samples
    ],
    ids=[
        f"{format_id}-{case}"
        for format_id, samples in INVALID_SAMPLES.items()
        for case, _, _ in samples
    ],
)
def test_invalid_samples_report_exact_error_rule_ids(
    format_id: str, case: str, content: str, expected: set[str]
) -> None:
    findings = validate_output(format_id, content)
    assert {
        finding.rule_id.removeprefix(f"etl.{format_id}.")
        for finding in findings
        if finding.severity == "error"
    } == expected, case
    assert all(finding.check == "syntax" for finding in findings)
    assert all(finding.rule_id.startswith(f"etl.{format_id}.") for finding in findings)
    assert findings == sorted(
        findings, key=lambda finding: (finding.line is None, finding.line or 0)
    )


@pytest.mark.parametrize(
    ("format_id", "content", "warning_rule"),
    [
        (
            "redshift.airflow_dag",
            "from airflow import DAG\nDAG('one')\nDAG('two')\n",
            "multiple_dags",
        ),
        (
            "snowflake.tasks_sql",
            "CREATE TASK root AS SELECT 1;",
            "root_without_schedule",
        ),
        (
            "snowflake.tasks_sql",
            "CREATE TASK root SCHEDULE = 'daily' AS SELECT 1;\n"
            "CREATE TASK child AFTER root AS SELECT 2;",
            None,
        ),
    ],
)
def test_warnings_and_supported_execution_as_options(
    format_id: str, content: str, warning_rule: str | None
) -> None:
    findings = validate_output(format_id, content)
    if warning_rule is None:
        assert findings == []
    else:
        assert [(finding.rule_id, finding.severity) for finding in findings] == [
            (f"etl.{format_id}.{warning_rule}", "warning")
        ]


def test_sql_dialect_quoting_and_masking_rules() -> None:
    assert validate_output("snowflake.sql", r"SELECT 'it\'s fine';") == []
    synapse_backslash = validate_output("synapse.sql", r"SELECT 'it\'s not escaped';")
    assert {finding.rule_id for finding in synapse_backslash} == {
        "etl.synapse.sql.unterminated_string"
    }
    assert validate_output("redshift.sql", "SELECT $body$) ; ' text$body$;") == []
    assert validate_output("bigquery.sql", "SELECT '''a ) ; ' text''' ;") == []
    assert validate_output("synapse.sql", "SELECT [a)] FROM [t];") == []
    assert (
        validate_output(
            "snowflake.tasks_sql",
            "CREATE TASK root SCHEDULE = 'daily' EXECUTE AS USER alice "
            "AS SELECT SYSTEM$STREAM_HAS_DATA('x');",
        )
        == []
    )


def _valid_job(tasks: list[dict[str, object]]) -> str:
    return json.dumps({"name": "job", "tasks": tasks})


def _job_task(task_key: str, dependencies: list[str] | None = None) -> dict[str, object]:
    task: dict[str, object] = {
        "task_key": task_key,
        "notebook_task": {"notebook_path": f"/{task_key}"},
    }
    if dependencies:
        task["depends_on"] = [{"task_key": dependency} for dependency in dependencies]
    return task


def test_layered_dag() -> None:
    tasks = []
    for layer in range(25):
        for slot in range(2):
            dependencies = [f"t{layer - 1}_{previous}" for previous in range(2)] if layer else []
            tasks.append(_job_task(f"t{layer}_{slot}", dependencies))
    tasks.reverse()

    assert validate_output("databricks.workflows_job", _valid_job(tasks)) == []


def test_long_chain() -> None:
    tasks = [_job_task(f"t{index}", [f"t{index - 1}"] if index else []) for index in range(2000)]

    assert validate_output("databricks.workflows_job", _valid_job(tasks)) == []


def test_two_cycles() -> None:
    tasks = [
        _job_task("A", ["B"]),
        _job_task("B", ["A"]),
        _job_task("C", ["D"]),
        _job_task("D", ["C"]),
    ]
    findings = validate_output("databricks.workflows_job", _valid_job(tasks))
    cycles = [
        finding.message for finding in findings if finding.rule_id.endswith(".dependency_cycle")
    ]

    assert cycles == [
        "Task dependency cycle: A -> B -> A.",
        "Task dependency cycle: C -> D -> C.",
    ]


def test_self_loop() -> None:
    findings = validate_output(
        "databricks.workflows_job",
        _valid_job([_job_task("A", ["A"])]),
    )

    assert [
        finding.message for finding in findings if finding.rule_id.endswith(".dependency_cycle")
    ] == ["Task dependency cycle: A -> A."]


def test_bigquery_double_quote_escape() -> None:
    assert validate_output("bigquery.sql", r'SELECT "a\"b";') == []
    assert validate_output("bigquery.sql", r"SELECT `a\`;") == []
    assert validate_output("snowflake.sql", r'SELECT "a\";') == []


def test_airflow_import_boundary() -> None:
    findings = validate_output(
        "redshift.airflow_dag",
        'import airflow_utils\nDAG("example")\n',
    )

    assert [finding.rule_id for finding in findings] == [
        "etl.redshift.airflow_dag.missing_airflow_import"
    ]


def test_safety_of_validator_sources_and_unexecuted_python_payload() -> None:
    package_dir = Path(__file__).resolve().parents[3] / "migraai" / "etl" / "outputs"
    for path in package_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"exec", "eval", "compile", "__import__"}
            if isinstance(node, ast.Import):
                assert all(alias.name != "importlib" for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert node.module != "importlib"
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert node.value.id not in {"importlib", "builtins"}

    findings = validate_output(
        "bigquery.composer_dag",
        'import os\nos.system("not-run")\nraise SystemExit("not-run")\n',
    )
    assert {finding.rule_id for finding in findings} == {
        "etl.bigquery.composer_dag.missing_airflow_import",
        "etl.bigquery.composer_dag.missing_dag",
    }
