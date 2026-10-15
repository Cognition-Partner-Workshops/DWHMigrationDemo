from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery.parsers.snowflake import Parser

GOLDEN_DIR = Path(__file__).parent / "golden"
STATEMENTS = [
    ("select", "SELECT id, amount + tax AS total FROM sales QUALIFY total > 0"),
    ("json_path", "SELECT payload:customer.name[0]::STRING FROM events"),
    ("stage_query", "SELECT $1, $2:id FROM @landing/path/file.csv"),
    ("time_travel", "SELECT id FROM orders AT(TIMESTAMP => '2025-01-01')"),
    ("flatten", "SELECT f.value FROM src, LATERAL FLATTEN(input => src.payload) f"),
    ("merge", "MERGE INTO t USING s ON t.id=s.id WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)"),
    ("create_table", "CREATE TABLE t (id NUMBER(10,0), value VARIANT DEFAULT PARSE_JSON('{}'))"),
    ("table_clone", "CREATE TABLE t2 CLONE t1"),
    ("create_view", "CREATE SECURE VIEW v AS SELECT id FROM t"),
    ("create_materialized_view", "CREATE MATERIALIZED VIEW mv AS SELECT id FROM t"),
    ("sequence", "CREATE SEQUENCE seq START WITH 1 INCREMENT BY 1 ORDER"),
    ("stage", "CREATE STAGE st URL='s3://bucket'"),
    ("file_format", "CREATE FILE FORMAT ff TYPE=CSV"),
    ("stream", "CREATE STREAM st ON TABLE t"),
    ("pipe", "CREATE PIPE p AS COPY INTO t FROM @st"),
    ("task", "CREATE TASK task1 WAREHOUSE wh AS CALL proc()"),
    ("masking_policy", "CREATE MASKING POLICY mp AS (v STRING) RETURNS STRING -> v"),
    ("grant", "GRANT CREATE TABLE ON SCHEMA s TO ROLE analyst"),
    ("revoke", "REVOKE SELECT ON TABLE t FROM USER analyst"),
    ("copy", "COPY INTO t FROM @st"),
    ("routine", "CREATE FUNCTION f(x INT) RETURNS INT LANGUAGE SQL AS $$SELECT x$$"),
    ("use_schema", "USE SCHEMA db.sch"),
]


@pytest.mark.parametrize(("name", "sql"), STATEMENTS, ids=[name for name, _ in STATEMENTS])
def test_snowflake_ast_golden(name: str, sql: str) -> None:
    result = Parser().parse(sql, f"{name}.sql")
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    actual = result.statements[0].to_dict()
    golden_path = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    assert json.loads(golden_path.read_text(encoding="utf-8")) == actual
