import pytest

from migraai.discovery import ast
from migraai.discovery.parsers.hive import Parser

CORPUS = [
    (
        "external_partitioned_table",
        "CREATE EXTERNAL TABLE IF NOT EXISTS `db`.`t` (id BIGINT COMMENT 'pk', "
        "tags ARRAY<STRING>, m MAP<STRING,ARRAY<INT>>, s STRUCT<a:INT,b:STRING>) "
        "COMMENT 'x' PARTITIONED BY (dt STRING) CLUSTERED BY (id) SORTED BY "
        "(id DESC) INTO 32 BUCKETS ROW FORMAT DELIMITED FIELDS TERMINATED BY "
        "'\\t' LINES TERMINATED BY '\\n' STORED AS TEXTFILE LOCATION 'hdfs:///x' "
        "TBLPROPERTIES ('skip.header.line.count'='1')",
        "CreateTable",
    ),
    (
        "serde_ctas",
        "CREATE TABLE t ROW FORMAT SERDE 'org.x.JsonSerDe' WITH SERDEPROPERTIES "
        "('a'='b') STORED AS INPUTFORMAT 'a.b' OUTPUTFORMAT 'c.d' AS SELECT * FROM s",
        "CreateTable",
    ),
    (
        "insert_partition_distribution",
        "INSERT OVERWRITE TABLE t PARTITION (dt='${hivevar:run_date}', region) "
        "SELECT a, region FROM s WHERE b RLIKE '^x' DISTRIBUTE BY region SORT BY a",
        "Insert",
    ),
    (
        "multi_insert",
        "FROM src s INSERT OVERWRITE TABLE a PARTITION (dt='1') SELECT s.x "
        "WHERE s.y > 1 INSERT INTO TABLE b SELECT s.z GROUP BY s.z",
        "DialectStatement",
    ),
    (
        "lateral_views",
        "SELECT e.id, t.tag FROM ev e LATERAL VIEW OUTER explode(e.tags) t AS tag "
        "LATERAL VIEW posexplode(e.arr) p AS pos, val CLUSTER BY e.id",
        "HiveQuery",
    ),
    ("set_dynamic_partition", "SET hive.exec.dynamic.partition.mode=nonstrict", "SetStatement"),
    ("set_hivevar", "SET hivevar:env=prod", "SetStatement"),
    ("add_jar", "ADD JAR hdfs:///libs/my-udf-1.0.jar", "DialectStatement"),
    (
        "temporary_function",
        "CREATE TEMPORARY FUNCTION myf AS 'com.x.MyF' USING JAR "
        "'hdfs:///a.jar', FILE 'hdfs:///b.txt'",
        "CreateRoutine",
    ),
    (
        "quotes_variables",
        "SELECT 'it\\'s', \"dq\", `weird col` FROM `sales_${hivevar:env}`.orders",
        "HiveQuery",
    ),
    (
        "operators",
        "SELECT a FROM t WHERE a REGEXP 'x' OR NOT b RLIKE 'y' AND c == 1 "
        "AND d <=> NULL AND 7 DIV 2 = 3",
        "HiveQuery",
    ),
    ("msck_repair", "MSCK REPAIR TABLE db.t", "DialectStatement"),
    (
        "multiple_add_partitions",
        "ALTER TABLE db.t ADD IF NOT EXISTS PARTITION (dt='2024-01-01') "
        "LOCATION '/p1' PARTITION (dt='2024-01-02')",
        "Alter",
    ),
    (
        "view",
        "CREATE VIEW IF NOT EXISTS v (a COMMENT 'x') COMMENT 'v' AS SELECT a FROM t",
        "CreateView",
    ),
    ("use_database", "USE analytics", "Use"),
    ("hiveconf_filter", "SELECT * FROM t WHERE dt = '${hiveconf:dt}'", "HiveQuery"),
]


@pytest.mark.parametrize(("name", "sql", "expected_type"), CORPUS, ids=[item[0] for item in CORPUS])
def test_hive_probe_corpus_parses_cleanly(name: str, sql: str, expected_type: str) -> None:
    result = Parser().parse(sql + ";", f"{name}.sql")
    assert len(result.statements) == 1
    assert not [issue for issue in result.issues if issue.severity in {"warning", "error"}], (
        result.issues
    )
    statement = result.statements[0]
    assert type(statement).__name__ == expected_type
    assert not any(isinstance(node, ast.Unparsed) for node in ast.walk(statement))
    if isinstance(statement, ast.DialectStatement):
        expected_names = {
            "multi_insert": "multi_insert",
            "add_jar": "add_resource",
            "msck_repair": "msck_repair",
        }
        assert statement.name == expected_names[name]
    if name == "multiple_add_partitions":
        assert isinstance(statement, ast.Alter)
        partitions = statement.actions[0].args["partitions"]
        assert len(partitions) == 2
