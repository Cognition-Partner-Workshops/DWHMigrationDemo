from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery.parsers.oracle import Parser

GOLDEN_DIR = Path(__file__).parent / "golden"
STATEMENTS = [
    (
        "hierarchical",
        "SELECT LEVEL, ROWNUM FROM oe.employees "
        "START WITH manager_id IS NULL CONNECT BY PRIOR employee_id = manager_id",
    ),
    ("bulk_select", "SELECT order_id BULK COLLECT INTO order_ids FROM oe.orders"),
    ("sequence_value", "SELECT order_seq.NEXTVAL, order_seq.CURRVAL FROM dual"),
    ("cursor_attribute", "SELECT SQL%ROWCOUNT FROM dual"),
    (
        "global_temporary_table",
        "CREATE GLOBAL TEMPORARY TABLE oe.stage (id NUMBER(12), label VARCHAR2(30 CHAR)) "
        "ON COMMIT DELETE ROWS",
    ),
    (
        "partitioned_table",
        "CREATE TABLE oe.sales (id NUMBER) PARTITION BY RANGE (id) "
        "(PARTITION old VALUES LESS THAN (10))",
    ),
    ("database_link", "SELECT order_id FROM oe.orders@reporting_link"),
    (
        "connect_by",
        "SELECT CONNECT_BY_ROOT employee_id FROM oe.employees "
        "CONNECT BY NOCYCLE PRIOR employee_id = manager_id",
    ),
    (
        "materialized_view",
        "CREATE MATERIALIZED VIEW oe.mv_orders BUILD IMMEDIATE REFRESH FAST ON COMMIT AS "
        "SELECT order_id FROM oe.orders",
    ),
    ("sequence", "CREATE SEQUENCE oe.order_seq START WITH 1 INCREMENT BY 1 NOCACHE NOCYCLE"),
    ("synonym", "CREATE PUBLIC SYNONYM order_alias FOR oe.orders"),
    ("create_index", "CREATE BITMAP INDEX oe.orders_status_bix ON oe.orders (status)"),
]


@pytest.mark.parametrize(("name", "sql"), STATEMENTS, ids=[name for name, _ in STATEMENTS])
def test_oracle_ast_golden_json(name: str, sql: str) -> None:
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
