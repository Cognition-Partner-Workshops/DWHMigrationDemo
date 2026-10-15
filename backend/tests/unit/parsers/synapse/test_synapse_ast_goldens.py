from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery.parsers.synapse import Parser

GOLDEN_DIR = Path(__file__).parent / "golden"
STATEMENTS = [
    (
        "ctas",
        "CREATE TABLE dbo.fact WITH (DISTRIBUTION = HASH(id)) AS SELECT id FROM dbo.stage",
    ),
    (
        "external_table",
        "CREATE EXTERNAL TABLE dbo.ext (id INT) WITH "
        "(LOCATION = '/data/', DATA_SOURCE = lake, FILE_FORMAT = csv)",
    ),
    (
        "data_source",
        "CREATE EXTERNAL DATA SOURCE lake WITH "
        "(TYPE = HADOOP, LOCATION = 'https://example.test/data')",
    ),
    (
        "file_format",
        "CREATE EXTERNAL FILE FORMAT csv WITH "
        "(FORMAT_TYPE = DELIMITEDTEXT, FORMAT_OPTIONS (FIELD_TERMINATOR = ','))",
    ),
    (
        "credential",
        "CREATE DATABASE SCOPED CREDENTIAL c WITH "
        "(IDENTITY = 'Managed Identity', SECRET = 'private')",
    ),
    ("master_key", "CREATE MASTER KEY ENCRYPTION BY PASSWORD = 'private'"),
    ("copy_into", "COPY INTO dbo.target FROM 'https://example.test/file.csv'"),
    (
        "workload_group",
        "CREATE WORKLOAD GROUP reporting WITH (MIN_PERCENTAGE_RESOURCE = 10)",
    ),
    ("rename", "RENAME OBJECT::dbo.old TO dbo.new"),
    ("statistics", "CREATE STATISTICS st ON dbo.target (id)"),
]


@pytest.mark.parametrize(("name", "sql"), STATEMENTS, ids=[name for name, _ in STATEMENTS])
def test_synapse_ast_golden_json(name: str, sql: str) -> None:
    result = Parser().parse(sql, f"{name}.sql")
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    actual = result.statements[0].to_dict()
    golden_path = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    assert json.loads(golden_path.read_text(encoding="utf-8")) == actual
