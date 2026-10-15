from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery.parsers.hive import Parser

GOLDEN_DIR = Path(__file__).parent / "golden"
STATEMENTS = [
    ("select_operators", "SELECT a == b, x <=> y, a DIV b, x & 1, x ^ 2, x | 4 FROM t"),
    ("regexp_operators", "SELECT text NOT RLIKE '^x', text REGEXP 'y' FROM t"),
    ("hive_variables", "SELECT sales_${hivevar:env} FROM ${hivevar:db}.orders"),
    ("from_first", "FROM src SELECT id, upper(name) AS name"),
    ("lateral_view", "SELECT e.value FROM src LATERAL VIEW OUTER explode(items) e AS value"),
    ("bucket_sample", "SELECT id FROM events TABLESAMPLE(BUCKET 2 OUT OF 8 ON id)"),
    (
        "multi_insert",
        "FROM src INSERT OVERWRITE TABLE out_a SELECT id INSERT INTO TABLE out_b SELECT name",
    ),
    (
        "insert_partition",
        "INSERT OVERWRITE TABLE events PARTITION (day='2025-01-01', region) SELECT * FROM src",
    ),
    (
        "insert_directory",
        "INSERT OVERWRITE LOCAL DIRECTORY 'hdfs:///tmp/out' ROW FORMAT DELIMITED "
        "FIELDS TERMINATED BY ',' STORED AS TEXTFILE SELECT id FROM src",
    ),
    (
        "create_complex_table",
        "CREATE EXTERNAL TABLE IF NOT EXISTS db.events "
        "(id BIGINT, attrs MAP<STRING, ARRAY<INT>>, "
        "payload STRUCT<name:STRING COMMENT 'label',active:BOOLEAN>) "
        "COMMENT 'events' PARTITIONED BY (day STRING) CLUSTERED BY (id) "
        "SORTED BY (id DESC) INTO 4 BUCKETS ROW FORMAT DELIMITED "
        "FIELDS TERMINATED BY ',' ESCAPED BY '\\\\' STORED AS ORC "
        "LOCATION 'hdfs:///warehouse/events' TBLPROPERTIES ('owner'='data')",
    ),
    ("create_ctas", "CREATE TABLE curated AS SELECT id FROM raw"),
    (
        "create_view",
        "CREATE VIEW IF NOT EXISTS db.v (id COMMENT 'key') COMMENT 'view' "
        "TBLPROPERTIES ('owner'='analytics') AS SELECT id FROM db.t",
    ),
    (
        "create_database",
        "CREATE DATABASE IF NOT EXISTS analytics COMMENT 'warehouse' LOCATION 'hdfs:///db'",
    ),
    (
        "create_function",
        "CREATE TEMPORARY FUNCTION normalize AS 'com.example.Normalize' "
        "USING JAR 'hdfs:///udf.jar', FILE 'hdfs:///config.json'",
    ),
    ("set_scoped", "SET hivevar:tenant=acme"),
    ("set_verbose", "SET -v"),
    ("set_empty", "SET"),
    ("reset", "RESET hivevar:tenant"),
    ("add_resource", "ADD JAR hdfs:///udf/library.jar"),
    ("list_resources", "LIST JARS"),
    ("delete_resource", "DELETE ARCHIVE hdfs:///udf/archive.zip"),
    (
        "load_data",
        "LOAD DATA LOCAL INPATH '/tmp/day.csv' OVERWRITE INTO TABLE events "
        "PARTITION (day='2025-01-01')",
    ),
    ("msck_repair", "MSCK REPAIR TABLE events"),
    ("analyze", "ANALYZE TABLE events PARTITION (day='2025-01-01') COMPUTE STATISTICS FOR COLUMNS"),
    (
        "query_distribution",
        "SELECT id FROM events DISTRIBUTE BY region SORT BY id DESC CLUSTER BY bucket LIMIT 10",
    ),
    (
        "alter_add_partition",
        "ALTER TABLE events ADD IF NOT EXISTS PARTITION (day='2025-01-01') LOCATION '/data/day'",
    ),
    (
        "alter_drop_partition",
        "ALTER TABLE events DROP IF EXISTS PARTITION (day='2025-01-01') PURGE",
    ),
    (
        "alter_change_column",
        "ALTER TABLE events CHANGE COLUMN old_name new_name STRING COMMENT 'name' AFTER id",
    ),
    ("alter_properties", "ALTER TABLE events SET TBLPROPERTIES ('owner'='warehouse')"),
    ("truncate_partition", "TRUNCATE TABLE events PARTITION (day='2025-01-01')"),
    ("drop_function", "DROP TEMPORARY FUNCTION IF EXISTS normalize"),
    ("show_tables", "SHOW TABLES IN analytics LIKE 'ev*'"),
    ("describe", "DESCRIBE FORMATTED TABLE events PARTITION (day='2025-01-01')"),
]


@pytest.mark.parametrize(("name", "sql"), STATEMENTS, ids=[name for name, _ in STATEMENTS])
def test_hive_ast_golden(name: str, sql: str) -> None:
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
