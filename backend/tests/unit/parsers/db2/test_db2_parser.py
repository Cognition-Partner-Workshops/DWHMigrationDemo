from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery import ast
from migraai.discovery.objects import extract as extract_objects
from migraai.discovery.parsers.db2 import Parser
from migraai.discovery.references import extract as extract_references

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDENS = [
    ("table", "CREATE TABLE BANK.ACCOUNTS (ID INT NOT NULL, NAME VARCHAR(40) WITH DEFAULT 'x')"),
    ("view", "CREATE VIEW BANK.ACCOUNT_VIEW AS SELECT ID, NAME FROM BANK.ACCOUNTS"),
    (
        "mqt",
        "CREATE TABLE BANK.ACCOUNT_MQT AS (SELECT ID FROM BANK.ACCOUNTS) "
        "DATA INITIALLY DEFERRED REFRESH DEFERRED",
    ),
    (
        "sequence",
        "CREATE SEQUENCE BANK.ACCOUNT_SEQ AS BIGINT START WITH 1 "
        "INCREMENT BY 1 NO CYCLE CACHE 20 ORDER",
    ),
    (
        "permission",
        "CREATE PERMISSION BANK.ACCOUNT_FILTER ON BANK.ACCOUNTS AS A "
        "FOR ROWS WHERE A.ID > 0 ENFORCED FOR ALL ACCESS ENABLE",
    ),
    (
        "mask",
        "CREATE MASK BANK.ACCOUNT_MASK ON BANK.ACCOUNTS AS A FOR COLUMN NAME "
        "RETURN CASE WHEN A.ID = 1 THEN 'x' ELSE NAME END ENABLE",
    ),
    ("alias", "CREATE PUBLIC ALIAS BANK.ACCOUNTS_ALIAS FOR TABLE BANK.ACCOUNTS"),
    (
        "grant",
        "GRANT SELECT, UPDATE ON TABLE BANK.ACCOUNTS TO USER APP, ROLE REPORTER WITH GRANT OPTION",
    ),
    (
        "index",
        "CREATE INDEX BANK.ACCOUNT_IDX ON BANK.ACCOUNTS (ID ASC, NAME DESC) "
        "INCLUDE (NAME) CLUSTER ALLOW REVERSE SCANS",
    ),
    (
        "hints",
        "SELECT ID FROM BANK.ACCOUNTS FETCH FIRST 10 ROWS ONLY WITH UR "
        "FOR READ ONLY OPTIMIZE FOR 10 ROWS",
    ),
    ("temporal", "SELECT ID FROM BANK.ACCOUNTS FOR SYSTEM_TIME AS OF CURRENT_TIMESTAMP"),
    ("values_into", "VALUES 1, 2 INTO :A, :B"),
]


@pytest.mark.parametrize(("name", "sql"), GOLDENS, ids=[name for name, _ in GOLDENS])
def test_db2_ast_golden_json(name: str, sql: str) -> None:
    result = Parser().parse(sql, f"{name}.sql")
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    actual = result.statements[0].to_dict()
    path = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == actual


def _one(sql: str) -> ast.Node:
    result = Parser().parse(sql)
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    return result.statements[0]


def test_db2_execute_prepared_statement_is_dynamic_sql_not_call() -> None:
    procedure = _one(
        "CREATE PROCEDURE BANK.PREPARED(IN P_ID INT, OUT P_VALUE INT) "
        "LANGUAGE SQL BEGIN "
        "PREPARE S1 FROM 'SELECT ID FROM BANK.ACCOUNT WHERE ID = ?'; "
        "EXECUTE S1 INTO P_VALUE, P_OTHER USING P_ID, :VALUE; END"
    )
    assert isinstance(procedure, ast.CreateRoutine)
    dynamic_statements = [node for node in procedure.walk() if isinstance(node, ast.DynamicSql)]
    prepared = next(node for node in dynamic_statements if isinstance(node.sql_expr, ast.Literal))
    executed = next(
        node for node in dynamic_statements if isinstance(node.sql_expr, ast.Identifier)
    )
    assert any(isinstance(statement, ast.Query) for statement in prepared.parsed)
    assert isinstance(executed.sql_expr, ast.Identifier)
    assert executed.sql_expr.value == "S1"
    assert [item.value for item in executed.into] == ["P_VALUE", "P_OTHER"]
    assert all(isinstance(item, ast.Identifier) for item in executed.into)
    assert isinstance(executed.using[0], ast.ColumnRef)
    assert isinstance(executed.using[1], ast.Param)
    references = extract_references(procedure, Parser)
    assert not any(
        ref.ref_kind == "calls" and ref.name.parts[-1].value.casefold() == "s1"
        for ref in references
    )
    assert any(
        ref.ref_kind == "reads" and ref.name.parts[-1].value.casefold() == "account"
        for ref in references
    )


def test_db2_dynamic_terminators_and_recovery() -> None:
    source = """--#SET TERMINATOR @
CREATE PROCEDURE BANK.P(IN A INT)
LANGUAGE SQL
BEGIN ATOMIC
  SET A = A + 1;
END @
--#SET TERMINATOR ;
CREATE TABLE BANK.GOOD (ID INT);
"""
    result = Parser().parse(source)
    assert not result.issues, result.issues
    assert isinstance(result.statements[0], ast.CreateRoutine)
    assert isinstance(result.statements[1], ast.CreateTable)
    assert Parser().parse("SELECT '@' FROM BANK.T").statements[0].body.projections
    broken = Parser().parse(
        "--#SET TERMINATOR @\nCREATE EVENT MONITOR BANK.BAD FOR DATABASE @\n"
        "CREATE TABLE BANK.AFTER (ID INT) @"
    )
    assert broken.issues
    assert any(isinstance(node, ast.CreateTable) for node in broken.statements)


def test_db2_terminator_inside_string_does_not_end_statement() -> None:
    result = Parser().parse(
        "--#SET TERMINATOR @\n"
        "CREATE TABLE BANK.LITERAL (TXT VARCHAR(2) DEFAULT '@')@\n"
        "CREATE TABLE BANK.AFTER_LITERAL (ID INT)@"
    )
    assert not result.issues, result.issues
    assert len(result.statements) == 2
    assert all(isinstance(node, ast.CreateTable) for node in result.statements)


def test_db2_clp_redaction_and_schema() -> None:
    parser = Parser()
    result = parser.parse("CONNECT TO BANK USER APP USING secret-password;\nSET SCHEMA = BANK;")
    serialized = json.dumps(result.to_dict())
    assert "secret-password" not in serialized
    assert isinstance(result.statements[0], ast.ClientCommand)
    assert isinstance(result.statements[1], ast.Use)
    assert parser.default_schema == "BANK"
    assert isinstance(Parser().parse("CONNECT RESET").statements[0], ast.ClientCommand)


def test_db2_routines_psm_and_external_options() -> None:
    routine = _one(
        "CREATE OR REPLACE PROCEDURE BANK.P(IN A INT, OUT B VARCHAR(10)) "
        "LANGUAGE SQL SPECIFIC BANK.P1 DYNAMIC RESULT SETS 1 READS SQL DATA "
        "NOT DETERMINISTIC NO EXTERNAL ACTION BEGIN ATOMIC "
        "DECLARE X INT DEFAULT 0; SET X = A; SET B = X; END"
    )
    assert isinstance(routine, ast.CreateRoutine)
    assert routine.options["specific"].parts[-1].value == "P1"
    assert routine.options["dynamic_result_sets"].value == "1"
    assert isinstance(routine.body, ast.Block)
    external = _one(
        "CREATE FUNCTION BANK.F(IN X INT) RETURNS INT LANGUAGE C "
        "EXTERNAL NAME 'lib!f' PARAMETER STYLE SQL"
    )
    assert isinstance(external, ast.CreateRoutine)
    assert external.body is None
    assert external.options["external_name"] == "lib!f"
    procedural = _one(
        "CREATE PROCEDURE BANK.LOOP(IN P_ACCOUNT INT) LANGUAGE SQL BEGIN ATOMIC "
        "DECLARE V INT DEFAULT 0; "
        "DECLARE EXIT HANDLER FOR SQLEXCEPTION "
        "SIGNAL SQLSTATE '75000' SET MESSAGE_TEXT = 'payment failed'; "
        "PAYMENT_LOOP: FOR V AS PAYMENT_CURSOR CURSOR FOR "
        "SELECT ID FROM BANK.ACCOUNT DO "
        "INSERT INTO BANK.PAYMENT (ACCOUNT_ID) VALUES (V); END FOR PAYMENT_LOOP; END"
    )
    assert isinstance(procedural, ast.CreateRoutine)
    assert isinstance(procedural.body, ast.Block)
    handler = next(
        declaration
        for declaration in procedural.body.declarations
        if isinstance(declaration, ast.HandlerDecl)
    )
    assert isinstance(handler.statements[0], ast.Signal)
    assert handler.statements[0].condition == "75000"
    loop = next(node for node in procedural.body.statements if isinstance(node, ast.ForLoop))
    assert isinstance(loop.iterable, ast.Query)
    assert isinstance(loop.statements[0], ast.Insert)


def test_db2_function_return_select_and_expression_bodies() -> None:
    select_function = _one(
        "CREATE FUNCTION BANK.ACCOUNT_COUNT() RETURNS INT LANGUAGE SQL "
        "RETURN SELECT COUNT(*) FROM BANK.ACCOUNT"
    )
    expression_function = _one(
        "CREATE FUNCTION BANK.NEXT_ACCOUNT_ID(IN P_ID INT) RETURNS INT LANGUAGE SQL RETURN P_ID + 1"
    )
    assert isinstance(select_function, ast.CreateRoutine)
    assert isinstance(select_function.body, ast.Return)
    assert isinstance(select_function.body.expr, ast.Query)
    assert isinstance(expression_function, ast.CreateRoutine)
    assert isinstance(expression_function.body, ast.Return)
    assert isinstance(expression_function.body.expr, ast.Binary)

    result = Parser().parse(
        "CREATE FUNCTION BANK.ACCOUNT_COUNT() RETURNS INT LANGUAGE SQL "
        "RETURN SELECT COUNT(*) FROM BANK.ACCOUNT"
    )
    obj = extract_objects(result, "functions.sql", Parser)[0]
    assert obj.status == "parsed"
    assert any(
        ref.name.parts[-1].value == "ACCOUNT" and ref.ref_kind == "reads"
        for ref in extract_references(result.statements[0], Parser)
    )


def test_db2_temporal_columns_mqt_and_alter_actions() -> None:
    table = _one(
        "CREATE TABLE BANK.HISTORY (ID INT GENERATED ALWAYS AS IDENTITY "
        "(START WITH 1, INCREMENT BY 1), "
        "VALID_FROM TIMESTAMP GENERATED ALWAYS AS ROW BEGIN, "
        "VALID_TO TIMESTAMP GENERATED ALWAYS AS ROW END, "
        "PERIOD SYSTEM_TIME (VALID_FROM, VALID_TO)) "
        "DISTRIBUTE BY HASH (ID) ORGANIZE BY ROW"
    )
    assert isinstance(table, ast.CreateTable)
    assert table.options["distribution"].name == "distribute_by"
    assert table.options["periods"][0].args["name"] == "system_time"
    storage = _one(
        "CREATE TABLE BANK.COLUMN_ORGANIZED (ID INT GENERATED ALWAYS AS IDENTITY "
        "(START WITH 1, INCREMENT BY 1)) IN BANK.DATA_TS INDEX IN BANK.INDEX_TS "
        "LONG IN BANK.LONG_TS DISTRIBUTE BY RANDOM ORGANIZE BY COLUMN COMPRESS YES ADAPTIVE "
        "PARTITION BY RANGE (ID) (STARTING 1 ENDING 100 EVERY 10)"
    )
    assert isinstance(storage, ast.CreateTable)
    assert storage.options["tablespace"].parts[-1].value == "DATA_TS"
    assert storage.options["index_tablespace"].parts[-1].value == "INDEX_TS"
    assert storage.options["long_tablespace"].parts[-1].value == "LONG_TS"
    assert storage.options["distribution"].args["random"] is True
    assert storage.options["organize_by"].args["kind"] == "column"
    assert storage.options["partition_by"].args["starting"].value == "1"
    assert storage.options["partition_by"].args["ending"].value == "100"
    assert storage.options["partition_by"].args["every"].value == "10"
    like_table = _one("CREATE TABLE BANK.ACCOUNT_COPY LIKE BANK.ACCOUNT")
    assert isinstance(like_table, ast.CreateTable)
    assert like_table.like.parts[-1].value == "ACCOUNT"
    altered = _one("ALTER TABLE BANK.HISTORY ADD VERSIONING USE HISTORY TABLE BANK.HISTORY_ARCHIVE")
    assert isinstance(altered, ast.Alter)
    assert altered.actions[0].name == "add_versioning"
    as_of = _one("SELECT ID FROM BANK.HISTORY FOR SYSTEM_TIME AS OF '2025-01-01'")
    between = _one(
        "SELECT ID FROM BANK.HISTORY FOR SYSTEM_TIME BETWEEN '2025-01-01' AND '2025-02-01'"
    )
    from_to = _one("SELECT ID FROM BANK.HISTORY FOR SYSTEM_TIME FROM '2025-01-01' TO '2025-02-01'")
    assert as_of.body.from_[0].temporal.args["value"].value == "2025-01-01"
    assert between.body.from_[0].temporal.args["end"].value == "2025-02-01"
    assert from_to.body.from_[0].temporal.args["value"].value == "2025-01-01"
    mqt = _one(
        "CREATE TABLE BANK.MQT AS (SELECT ID FROM BANK.ACCOUNTS) "
        "DATA INITIALLY DEFERRED REFRESH DEFERRED ENABLE QUERY OPTIMIZATION"
    )
    assert isinstance(mqt, ast.DialectStatement)
    assert mqt.args["object_type"] == "materialized_query_table"
    refreshed = _one("REFRESH TABLE BANK.ACCOUNT_MQT")
    assert isinstance(refreshed, ast.DialectStatement)
    assert refreshed.name == "refresh_table"
    row_access = _one("ALTER TABLE BANK.ACCOUNT ACTIVATE ROW ACCESS CONTROL")
    assert isinstance(row_access, ast.Alter)
    assert row_access.actions[0].name == "activate_access_control"


def test_db2_rcac_alias_references_and_sequence_expressions() -> None:
    permission = _one(
        "CREATE PERMISSION BANK.P ON BANK.T AS T FOR ROWS WHERE T.ID IN "
        "(SELECT ID FROM BANK.ALLOWED) ENABLE"
    )
    assert isinstance(permission, ast.DialectStatement)
    assert permission.args["object_type"] == "row_permission"
    assert any(ref.ref_kind == "grant_on" for ref in extract_references(permission, Parser))
    mask = _one("CREATE MASK BANK.M ON BANK.T FOR COLUMN SSN RETURN 'x' ENABLE")
    assert isinstance(mask, ast.DialectStatement)
    assert mask.args["object_type"] == "column_mask"
    alias = _one("CREATE ALIAS BANK.A FOR TABLE BANK.T")
    objects = extract_objects(
        Parser().parse("CREATE ALIAS BANK.A FOR TABLE BANK.T"), "a.sql", Parser
    )
    assert isinstance(alias, ast.DialectStatement)
    assert objects[0].type == "alias"
    query = _one("SELECT NEXT VALUE FOR BANK.S, PREVIOUS VALUE FOR BANK.S FROM BANK.T")
    assert isinstance(query, ast.Query)
    assert query.body.projections[0].expr.name == "next_value"
    assert query.body.projections[1].expr.name == "previous_value"


def test_db2_grants_indexes_casts_and_crlf() -> None:
    grant = _one("GRANT EXECUTE ON PROCEDURE BANK.P TO ROLE APP")
    assert isinstance(grant, ast.Grant)
    assert grant.privileges == ["EXECUTE"]
    index = _one("CREATE INDEX BANK.I ON BANK.T (A DESC) ALLOW REVERSE SCANS")
    assert isinstance(index, ast.CreateIndex)
    assert index.options["columns"][0].args["order"] == "desc"
    cast = _one("SELECT 1::INT FROM BANK.T")
    assert isinstance(cast, ast.Query)
    crlf = Parser().parse("CREATE TABLE BANK.CRLF (ID INT)\r\n")
    assert not crlf.issues


def test_db2_query_hints_synonyms_and_set_commands() -> None:
    query = _one(
        "SELECT ID FROM BANK.ACCOUNT FETCH FIRST 10 ROWS ONLY "
        "WITH UR FOR READ ONLY OPTIMIZE FOR 5 ROWS"
    )
    assert isinstance(query, ast.Query)
    assert query.limit.fetch_first is True
    assert query.limit.count.value == "10"
    assert query.body.hints == ["with ur", "for read only", "optimize for 5 rows"]
    synonym = _one("CREATE SYNONYM BANK.ACCOUNT_SYNONYM FOR BANK.ACCOUNT")
    assert isinstance(synonym, ast.CreateSynonym)
    assert synonym.target.parts[-1].value == "ACCOUNT"
    path = _one("SET PATH = BANK, SYSIBM")
    assert isinstance(path, ast.SetStatement)
    assert len(path.items) == 2
    degree = _one("SET CURRENT DEGREE = 'ANY'")
    assert isinstance(degree, ast.SetStatement)
    assert degree.scope == "current"
    values = _one("VALUES 1 INTO :VALUE")
    assert isinstance(values, ast.DialectStatement)
    assert values.name == "values_into"
    assert values.args["values"][0].value == "1"
    assert values.args["targets"][0].value == ":VALUE"
    allocate = _one("ALLOCATE RESULT_CURSOR CURSOR FOR RESULT SET RESULT_LOCATION")
    assert isinstance(allocate, ast.DialectStatement)
    assert allocate.name == "allocate_result_set_cursor"
    prepared = _one("PREPARE S FROM 'SELECT ID FROM BANK.ACCOUNT'")
    assert isinstance(prepared, ast.DynamicSql)
    assert any(isinstance(statement, ast.Query) for statement in prepared.parsed)
    executed = _one("EXECUTE S USING :VALUE")
    assert isinstance(executed, ast.DynamicSql)
    assert isinstance(executed.using[0], ast.Param)
    assert executed.using[0].text == ":VALUE"
    immediate = _one("EXECUTE IMMEDIATE 'UPDATE BANK.ACCOUNT SET BALANCE = 1'")
    assert isinstance(immediate, ast.DynamicSql)
