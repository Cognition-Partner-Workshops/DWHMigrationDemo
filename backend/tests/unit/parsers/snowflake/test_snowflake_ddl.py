from migraai.discovery import ast
from migraai.discovery.parsers.snowflake import Parser
from migraai.discovery.registry import get_sql_parser


def _parse(sql: str) -> ast.Node:
    result = Parser().parse(sql, "ddl.sql")
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    return result.statements[0]


def test_snowflake_table_columns_constraints_and_options() -> None:
    table = _parse(
        "CREATE TRANSIENT TABLE IF NOT EXISTS db.t "
        "(id NUMBER(10,0) AUTOINCREMENT(1,1) ORDER, value VARIANT DEFAULT PARSE_JSON('{}') "
        "COMMENT 'payload', CONSTRAINT pk PRIMARY KEY (id)) CLUSTER BY (id)"
    )
    assert isinstance(table, ast.CreateTable) and table.options["transient"]
    assert table.if_not_exists and table.columns[0].identity.name == "identity"
    assert table.options["cluster_by"]
    assert table.constraints[0].constraint_kind == "primary_key"


def test_snowflake_structured_types_and_inline_policy_options() -> None:
    table = _parse(
        "CREATE TABLE typed (arr ARRAY(NUMBER), obj OBJECT(a INT, b STRING), "
        "mapping MAP(VARCHAR, NUMBER), vector VECTOR(FLOAT, 256), "
        "masked STRING WITH MASKING POLICY mp USING (id) WITH TAG (sensitivity='high'))"
    )
    assert isinstance(table, ast.CreateTable)
    assert [column.type.name.parts[-1].value for column in table.columns[:4]] == [
        "ARRAY",
        "OBJECT",
        "MAP",
        "VECTOR",
    ]
    assert isinstance(table.columns[1].type.children[0], ast.StructField)
    assert table.columns[4].options["masking_policy"].parts[-1].value == "mp"
    assert table.columns[4].options["tags"]["sensitivity"].value == "high"


def test_snowflake_clone_ctas_view_and_sequence() -> None:
    assert isinstance(_parse("CREATE TABLE clone CLONE source AT(OFFSET => -60)"), ast.CreateTable)
    ctas = _parse("CREATE TABLE copy AS SELECT id FROM src")
    assert isinstance(ctas, ast.CreateTable) and ctas.as_query
    view = _parse("CREATE OR REPLACE SECURE MATERIALIZED VIEW v AS SELECT id FROM src")
    assert isinstance(view, ast.CreateView) and view.secure and view.materialized and view.replace
    sequence = _parse("CREATE SEQUENCE seq START WITH 3 INCREMENT BY 2 NOORDER COMMENT = 'ids'")
    assert isinstance(sequence, ast.CreateSequence) and sequence.options["increment"]


def test_snowflake_platform_objects_and_policy_shapes() -> None:
    stage = _parse("CREATE STAGE st URL='s3://bucket' FILE_FORMAT=(FORMAT_NAME=ff)")
    assert isinstance(stage, ast.DialectStatement) and stage.args["object_type"] == "stage"
    assert stage.references[0].ref_kind == "reads"
    stream = _parse("CREATE STREAM changes ON TABLE raw.orders")
    assert isinstance(stream, ast.DialectStatement) and stream.args["on_kind"] == "table"
    pipe = _parse("CREATE PIPE p AUTO_INGEST = TRUE AS COPY INTO t FROM @st")
    assert isinstance(pipe, ast.DialectStatement)
    assert pipe.args["properties"]["auto_ingest"].value == "true"
    task = _parse("CREATE TASK t SCHEDULE = '5 minutes' AS COPY INTO t FROM @st")
    assert isinstance(task, ast.DialectStatement)
    assert task.args["properties"]["schedule"].value == "5 minutes"
    policy = _parse(
        "CREATE MASKING POLICY mask AS (v STRING) RETURNS STRING -> IFF(v = 'x', '***', v)"
    )
    assert isinstance(policy, ast.DialectStatement) and policy.args["params"][0].name.value == "v"
    row_policy = _parse(
        "CREATE ROW ACCESS POLICY row_filter AS (r STRING) RETURNS BOOLEAN -> EXISTS "
        "(SELECT 1 FROM allowed WHERE allowed.name = r)"
    )
    assert isinstance(row_policy, ast.DialectStatement) and row_policy.args["body"]


def test_snowflake_grants_and_principal_context() -> None:
    grant = _parse("GRANT APPLY MASKING POLICY ON TABLE t TO ROLE analyst")
    assert isinstance(grant, ast.Grant)
    assert grant.privileges == ["apply masking policy"]
    assert grant.grantee_kinds == ["role"]
    revoke = _parse("REVOKE ALL PRIVILEGES ON FUTURE TABLES IN SCHEMA s FROM SHARE partner")
    assert isinstance(revoke, ast.Revoke)
    assert revoke.object_kind == "future_tables_in_schema"
    assert revoke.grantee_kinds == ["share"]


def test_snowflake_copy_set_use_and_registry() -> None:
    copy = _parse(
        "COPY INTO raw.orders FROM @landing/orders FILES=('orders.csv') "
        "FILE_FORMAT=(FORMAT_NAME=ff) ON_ERROR='CONTINUE'"
    )
    assert isinstance(copy, ast.DialectStatement) and copy.args["direction"] == "load"
    assert any(ref.ref_kind == "writes" for ref in copy.references)
    assert isinstance(_parse("SET (a, b) = (1, 2)"), ast.SetStatement)
    parser = Parser()
    result = parser.parse("USE DATABASE sales; USE SCHEMA curated", "use.sql")
    assert isinstance(result.statements[-1], ast.Use)
    assert parser.default_schema == "sales.curated"
    assert get_sql_parser("snowflake") is Parser


def test_snowflake_alter_task_share_and_policy_actions() -> None:
    task = _parse("ALTER TASK refresh_task SUSPEND")
    assert isinstance(task, ast.Alter) and task.actions[0].name == "suspend"
    share = _parse("ALTER SHARE s ADD ACCOUNTS = ('org1', 'org2')")
    assert isinstance(share, ast.Alter)
    assert len(share.actions[0].args["accounts"]) == 2
    policy = _parse("ALTER TABLE t MODIFY COLUMN ssn SET MASKING POLICY p")
    assert isinstance(policy, ast.Alter)
    assert policy.actions[0].references[0].ref_kind == "reads"
