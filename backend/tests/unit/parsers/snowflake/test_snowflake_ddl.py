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


def test_snowflake_identity_temporary_transient_and_cluster_options() -> None:
    identity = _parse(
        "CREATE TEMPORARY TABLE temp_t (id NUMBER IDENTITY(1,1), label STRING DEFAULT 'x')"
    )
    assert isinstance(identity, ast.CreateTable) and identity.temporary
    assert identity.columns[0].identity.args["start"].value == "1"
    assert identity.columns[0].identity.args["increment"].value == "1"
    transient = _parse("CREATE TRANSIENT TABLE transient_t (id NUMBER) CLUSTER BY (id)")
    assert isinstance(transient, ast.CreateTable)
    assert transient.options["transient"] and transient.options["cluster_by"]


def test_snowflake_routine_language_bodies_are_strings() -> None:
    javascript = _parse(
        "CREATE FUNCTION js(x FLOAT) RETURNS FLOAT LANGUAGE JAVASCRIPT AS $$return x * 2;$$"
    )
    python = _parse(
        "CREATE FUNCTION py(x INT) RETURNS INT LANGUAGE PYTHON "
        "RUNTIME_VERSION = '3.11' HANDLER = 'f' AS $$def f(x):\n  return x$$"
    )
    assert isinstance(javascript, ast.CreateRoutine)
    assert javascript.language == "javascript" and isinstance(javascript.body, str)
    assert isinstance(python, ast.CreateRoutine)
    assert python.language == "python" and isinstance(python.body, str)


def test_snowflake_scripting_procedure_constructs_are_typed() -> None:
    routine = _parse(
        "CREATE PROCEDURE p(x NUMBER) RETURNS STRING LANGUAGE SQL AS $$ "
        "DECLARE c NUMBER DEFAULT 0; e EXCEPTION (-20001, 'bad'); BEGIN "
        "LET n NUMBER := :x + 1; FOR i IN 1 TO n DO c := c + i; END FOR; "
        "IF (c > 10) THEN RETURN 'large'; ELSE RETURN 'small'; END IF; "
        "EXCEPTION WHEN STATEMENT_ERROR OR EXPRESSION_ERROR THEN RETURN SQLERRM; "
        "WHEN OTHER THEN RAISE; END; $$"
    )
    assert isinstance(routine, ast.CreateRoutine) and isinstance(routine.body, ast.Block)
    block = routine.body
    assert [decl.name.value for decl in block.declarations] == ["c", "e"]
    assert isinstance(block.declarations[1], ast.Declare)
    assert block.declarations[1].type.name.parts[-1].value == "EXCEPTION"
    let_declare = next(node for node in block.statements if isinstance(node, ast.Declare))
    assert let_declare.name.value == "n"
    loop = next(node for node in block.statements if isinstance(node, ast.ForLoop))
    assert isinstance(loop.iterable, ast.Range)
    assert any(isinstance(node, ast.Assign) for node in ast.walk(loop))
    assert any(isinstance(node, ast.If) for node in block.statements)
    assert len(block.exception_handlers) == 2
    assert block.exception_handlers[0].conditions == ["STATEMENT_ERROR", "EXPRESSION_ERROR"]


def test_snowflake_tags_and_shares() -> None:
    tag = _parse("CREATE TAG sensitivity ALLOWED_VALUES 'public','restricted'")
    assert isinstance(tag, ast.DialectStatement) and tag.name == "create_tag"
    assert [value.value for value in tag.args["properties"]["allowed_values"]] == [
        "public",
        "restricted",
    ]
    share = _parse("CREATE SHARE partner_share")
    assert isinstance(share, ast.DialectStatement) and share.name == "create_share"
    grant = _parse("GRANT USAGE ON DATABASE analytics TO SHARE partner_share")
    assert isinstance(grant, ast.Grant) and grant.grantee_kinds == ["share"]
    alter = _parse("ALTER SHARE partner_share ADD ACCOUNTS = org1.account1")
    assert isinstance(alter, ast.Alter) and alter.actions[0].name == "add_accounts"


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
