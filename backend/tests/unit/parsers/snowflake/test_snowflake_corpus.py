import pytest

from migraai.discovery import ast
from migraai.discovery.parsers.snowflake import Parser

CORPUS = [
    (
        "transient_table",
        "CREATE OR REPLACE TRANSIENT TABLE a.b.t (id NUMBER AUTOINCREMENT START 1 "
        "INCREMENT 1, v VARIANT, o OBJECT, arr ARRAY, ts TIMESTAMP_NTZ "
        "DEFAULT CURRENT_TIMESTAMP()) CLUSTER BY (id, TO_DATE(ts))",
        "CreateTable",
    ),
    (
        "temporary_identity_table",
        "CREATE TEMPORARY TABLE t2 (id NUMBER IDENTITY(1,1), s STRING DEFAULT 'x')",
        "CreateTable",
    ),
    (
        "json_path",
        'SELECT v:customer.address[0]::string AS city, v:"Name"::varchar FROM raw r',
        "Query",
    ),
    (
        "flatten_qualify",
        "SELECT f.value:id::int FROM raw r, LATERAL FLATTEN(input => r.v:items) f "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY f.value:id ORDER BY 1) = 1",
        "Query",
    ),
    (
        "table_flatten",
        "SELECT * FROM TABLE(FLATTEN(input => PARSE_JSON('[1]'))) f",
        "Query",
    ),
    (
        "time_travel_at",
        "SELECT * FROM t AT(TIMESTAMP => '2024-01-01'::timestamp_ltz) x",
        "Query",
    ),
    ("time_travel_before", "SELECT * FROM t BEFORE(STATEMENT => '8e5d0ca9')", "Query"),
    ("time_travel_offset", "SELECT * FROM t AT(OFFSET => -60*5)", "Query"),
    ("identifier_from", "SELECT * FROM IDENTIFIER('db.sch.tbl')", "Query"),
    ("use_database", "USE DATABASE analytics", "Use"),
    ("use_schema", "USE SCHEMA analytics.raw", "Use"),
    ("use_warehouse", "USE WAREHOUSE wh", "DialectStatement"),
    (
        "scripting_procedure",
        "CREATE OR REPLACE PROCEDURE p(x NUMBER) RETURNS STRING LANGUAGE SQL AS $$\n"
        "DECLARE\n"
        "  c NUMBER DEFAULT 0;\n"
        "  e EXCEPTION (-20001, 'bad');\n"
        "BEGIN\n"
        "  LET n NUMBER := :x + 1;\n"
        "  FOR i IN 1 TO n DO\n"
        "    c := c + i;\n"
        "  END FOR;\n"
        "  IF (c > 10) THEN\n"
        "    INSERT INTO log_t SELECT :c;\n"
        "  ELSEIF (c = 0) THEN\n"
        "    RAISE e;\n"
        "  ELSE\n"
        "    RETURN 'small';\n"
        "  END IF;\n"
        "  RETURN 'ok';\n"
        "EXCEPTION\n"
        "  WHEN STATEMENT_ERROR OR EXPRESSION_ERROR THEN\n"
        "    RETURN SQLERRM;\n"
        "  WHEN OTHER THEN\n"
        "    RAISE;\n"
        "END;\n"
        "$$",
        "CreateRoutine",
    ),
    (
        "javascript_function",
        "CREATE FUNCTION js(x FLOAT) RETURNS FLOAT LANGUAGE JAVASCRIPT AS $$ return X*2; $$",
        "CreateRoutine",
    ),
    (
        "python_function",
        "CREATE OR REPLACE FUNCTION py(x INT) RETURNS INT LANGUAGE PYTHON "
        "RUNTIME_VERSION = '3.11' HANDLER = 'f' AS $$\ndef f(x):\n  return x\n$$",
        "CreateRoutine",
    ),
    ("secure_view", "CREATE SECURE VIEW sv AS SELECT * FROM t", "CreateView"),
    (
        "materialized_view",
        "CREATE OR REPLACE MATERIALIZED VIEW mv CLUSTER BY (a) AS SELECT a FROM t",
        "CreateView",
    ),
    ("tag", "CREATE TAG tg ALLOWED_VALUES 'a','b'", "DialectStatement"),
    (
        "alter_masking_policy",
        "ALTER TABLE t MODIFY COLUMN email SET MASKING POLICY mp",
        "Alter",
    ),
    (
        "alter_row_access_policy",
        "ALTER TABLE t ADD ROW ACCESS POLICY rap ON (region)",
        "Alter",
    ),
    ("alter_tag", "ALTER TABLE t SET TAG tg = 'pii'", "Alter"),
    ("alter_share", "ALTER SHARE s ADD ACCOUNTS = org1.acct2", "Alter"),
    (
        "grant_to_share",
        "GRANT SELECT ON ALL TABLES IN SCHEMA d.s TO SHARE s",
        "SnowflakeGrant",
    ),
    (
        "copy_unload",
        "COPY INTO @stg/unload/ FROM (SELECT * FROM t) FILE_FORMAT = (TYPE = PARQUET) "
        "HEADER = TRUE",
        "DialectStatement",
    ),
    (
        "copy_load",
        "COPY INTO t (a, b) FROM (SELECT $1, $2:id FROM @stg/x) "
        "PATTERN = '.*csv' ON_ERROR = CONTINUE",
        "DialectStatement",
    ),
    ("stream_properties", "CREATE STREAM s ON VIEW v APPEND_ONLY = TRUE", "DialectStatement"),
    (
        "task_warehouse_call",
        "CREATE TASK t2 WAREHOUSE = wh SCHEDULE = 'USING CRON 0 * * * * UTC' AS CALL p(1)",
        "DialectStatement",
    ),
    (
        "task_after",
        "CREATE TASK t3 AFTER t1, t2 AS EXECUTE IMMEDIATE $$BEGIN INSERT INTO x SELECT 1; END;$$",
        "DialectStatement",
    ),
    ("sequence", "CREATE SEQUENCE sq START = 1 INCREMENT = 5", "CreateSequence"),
    (
        "merge",
        "MERGE INTO t USING (SELECT * FROM s) src ON t.id = src.id WHEN MATCHED "
        "AND src.d THEN DELETE WHEN NOT MATCHED THEN INSERT (id) VALUES (src.id)",
        "Merge",
    ),
    ("set_subquery", "SET v = (SELECT MAX(id) FROM t)", "SetStatement"),
    (
        "ilike_iff",
        "SELECT a FROM t WHERE x ILIKE ANY ('a%', 'b%') AND IFF(a::int + 1 > 2, TRUE, FALSE)",
        "Query",
    ),
    (
        "execute_immediate",
        "EXECUTE IMMEDIATE $$ DECLARE r RESULTSET DEFAULT (SELECT 1); "
        "BEGIN RETURN TABLE(r); END; $$",
        "DynamicSql",
    ),
]


@pytest.mark.parametrize(("name", "sql", "expected_type"), CORPUS, ids=[item[0] for item in CORPUS])
def test_snowflake_probe_corpus_parses_cleanly(name: str, sql: str, expected_type: str) -> None:
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
            "use_warehouse": "use_warehouse",
            "tag": "create_tag",
            "copy_unload": "copy_into",
            "copy_load": "copy_into",
            "stream_properties": "create_stream",
            "task_warehouse_call": "create_task",
            "task_after": "create_task",
        }
        assert statement.name == expected_names[name]
    if name == "scripting_procedure":
        assert isinstance(statement, ast.CreateRoutine)
        assert isinstance(statement.body, ast.Block)
        body_nodes = list(ast.walk(statement.body))
        assert any(isinstance(node, ast.ForLoop) for node in body_nodes)
        assert any(isinstance(node, ast.If) for node in body_nodes)
        assert any(isinstance(node, ast.ExceptionHandler) for node in body_nodes)
        assert any(isinstance(node, ast.Declare) for node in body_nodes)
        assert any(isinstance(node, ast.Assign) for node in body_nodes)
    elif name in {"javascript_function", "python_function"}:
        assert isinstance(statement, ast.CreateRoutine)
        assert isinstance(statement.body, str)
        expected_language = "javascript" if name == "javascript_function" else "python"
        assert statement.language == expected_language
    elif name == "time_travel_before":
        assert isinstance(statement, ast.Query)
        table = statement.body.from_[0]
        assert isinstance(table, ast.TableRef)
        assert table.temporal is not None
        assert table.temporal.args["mode"] == "before"
