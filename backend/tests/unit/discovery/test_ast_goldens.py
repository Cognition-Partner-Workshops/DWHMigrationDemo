from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery.parsers.ansi import Parser

GOLDEN_DIR = Path(__file__).parent / "golden"
STATEMENTS = [
    ("select", "SELECT id, amount + tax AS total FROM sales WHERE id BETWEEN 1 AND 3"),
    ("cte", "WITH active AS (SELECT id FROM source_table) SELECT id FROM active"),
    ("join", "SELECT a.id FROM a LEFT JOIN b ON a.id = b.id"),
    (
        "window",
        "SELECT SUM(amount) OVER (PARTITION BY customer_id ORDER BY id ROWS 2 PRECEDING) FROM t",
    ),
    ("grouping", "SELECT region FROM sales GROUP BY ROLLUP(region), CUBE(channel)"),
    ("insert", "INSERT INTO target_table (id) VALUES (1)"),
    ("update", "UPDATE target_table SET id = 2 WHERE id = 1"),
    ("delete", "DELETE FROM target_table WHERE id = 1"),
    (
        "merge",
        "MERGE INTO target_table USING source_table ON target_table.id = source_table.id "
        "WHEN MATCHED THEN DELETE",
    ),
    (
        "create_table",
        "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT REFERENCES parent(id))",
    ),
    ("create_view", "CREATE VIEW v AS SELECT id FROM source_table"),
    ("create_index", "CREATE INDEX ix ON target_table (id)"),
    ("grant", "GRANT SELECT ON TABLE target_table TO analyst WITH GRANT OPTION"),
    ("revoke", "REVOKE SELECT ON TABLE target_table FROM analyst"),
    ("procedure", "CREATE PROCEDURE p() BEGIN CALL q(); END"),
    ("function", "CREATE FUNCTION f(x INT) RETURNS INT RETURN x + 1"),
    ("block", "BEGIN DECLARE x INT DEFAULT 1; SET x = x + 1; END"),
    ("dynamic_sql", "EXECUTE IMMEDIATE 'SELECT id FROM source_table'"),
    ("range_loop", "FOR i IN 1..3 DO SET x = i; END FOR"),
    ("alter", "ALTER TABLE target_table ADD COLUMN value INT"),
]


@pytest.mark.parametrize(("name", "sql"), STATEMENTS, ids=[name for name, _ in STATEMENTS])
def test_ast_golden_json(name: str, sql: str) -> None:
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
