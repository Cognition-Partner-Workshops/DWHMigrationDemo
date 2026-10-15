from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery import ast
from migraai.discovery.objects import extract as extract_objects
from migraai.discovery.parsers.netezza import Parser
from migraai.discovery.references import extract as extract_references

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDENS = [
    (
        "table",
        "CREATE TABLE LOGISTICS.SHIPMENT (ID INT, STATUS VARCHAR(20)) "
        "DISTRIBUTE ON (ID) ORGANIZE ON (STATUS)",
    ),
    (
        "ctas",
        "CREATE TABLE LOGISTICS.SHIPMENT_COPY AS SELECT ID FROM LOGISTICS.SHIPMENT "
        "DISTRIBUTE ON RANDOM",
    ),
    (
        "external_table",
        "CREATE EXTERNAL TABLE LOGISTICS.LOAD_STAGE (ID INT) "
        "USING (DATAOBJECT ('/x.csv') DELIMITER '|')",
    ),
    (
        "view",
        "CREATE VIEW LOGISTICS.SHIPPED AS SELECT ID FROM LOGISTICS.SHIPMENT "
        "WHERE STATUS = 'shipped'",
    ),
    (
        "materialized_view",
        "CREATE MATERIALIZED VIEW LOGISTICS.SHIPMENT_MV AS "
        "SELECT ID FROM LOGISTICS.SHIPMENT ORDER BY ID",
    ),
    (
        "sequence",
        "CREATE SEQUENCE LOGISTICS.SHIPMENT_SEQ AS BIGINT START WITH 1 INCREMENT BY 1 NO CYCLE",
    ),
    ("synonym", "CREATE SYNONYM LOGISTICS.SHIPMENTS FOR LOGISTICS.SHIPMENT"),
    (
        "procedure",
        "CREATE PROCEDURE LOGISTICS.PING(INTEGER) RETURNS INTEGER EXECUTE AS OWNER "
        "LANGUAGE NZPLSQL AS BEGIN_PROC BEGIN RETURN 1; END; END_PROC",
    ),
    ("grant", "GRANT SELECT, INSERT ON LOGISTICS.SHIPMENT TO GROUP OPS"),
    ("group", "CREATE GROUP OPS WITH USER ETL_USER"),
    ("groom", "GROOM TABLE LOGISTICS.SHIPMENT RECORDS ALL PAGES START VERSIONS"),
    ("statistics", "GENERATE EXPRESS STATISTICS ON LOGISTICS.SHIPMENT (ID, STATUS)"),
]


@pytest.mark.parametrize(("name", "sql"), GOLDENS, ids=[name for name, _ in GOLDENS])
def test_netezza_ast_golden_json(name: str, sql: str) -> None:
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


def test_netezza_distribution_ctas_and_qualified_names() -> None:
    table = _one("CREATE TEMP TABLE DB..T (ID INT) DISTRIBUTE ON (ID) ORGANIZE ON NONE")
    assert isinstance(table, ast.CreateTable)
    assert table.temporary
    assert table.name.to_dict()["parts"][1]["value"] == ""
    assert table.options["distribution"].name == "distribute_on"
    assert table.options["organize_on"].args["none"] is True
    ctas = _one("CREATE TABLE DB.SCHEMA.COPY AS SELECT ID FROM DB..T DISTRIBUTE ON RANDOM")
    assert isinstance(ctas, ast.CreateTable)
    assert isinstance(ctas.as_query, ast.Query)
    assert ctas.options["distribution"].args["random"] is True
    assert any(ref.ref_kind == "reads" for ref in extract_references(ctas, Parser))
    organized = _one(
        "CREATE TABLE LOGISTICS.ORGANIZED (A INT, B INT) DISTRIBUTE ON RANDOM ORGANIZE ON (A, B)"
    )
    assert organized.options["distribution"].args["random"] is True
    assert [column.value for column in organized.options["organize_on"].args["columns"]] == [
        "A",
        "B",
    ]


def test_netezza_external_tables_and_unload() -> None:
    external = _one(
        "CREATE EXTERNAL TABLE LOGISTICS.STAGE SAMEAS LOGISTICS.SHIPMENT "
        "USING (DATAOBJECT ('/data/shipments.csv') DELIMITER '|' SKIPROWS 1)"
    )
    assert isinstance(external, ast.CreateTable)
    assert external.external
    assert external.like.parts[-1].value == "SHIPMENT"
    assert external.options["using"]["skiprows"].value == "1"
    synonym = _one("CREATE SYNONYM LOGISTICS.SHIPMENT_ALIAS FOR LOGISTICS.SHIPMENT")
    assert isinstance(synonym, ast.CreateSynonym)
    assert synonym.target.parts[-1].value == "SHIPMENT"
    query = _one("SELECT * FROM EXTERNAL '/data/shipments.csv' USING (DELIMITER '|')")
    assert isinstance(query, ast.Query)
    assert isinstance(query.body.from_[0], ast.DialectStatement)
    unload = _one(
        "CREATE EXTERNAL TABLE '/out/shipments.csv' USING (DELIMITER '|') "
        "AS SELECT ID FROM LOGISTICS.SHIPMENT"
    )
    assert isinstance(unload, ast.DialectStatement)
    assert unload.name == "external_unload"
    assert any(ref.ref_kind == "reads" for ref in extract_references(unload, Parser))


def test_netezza_nzplsql_declarations_control_flow_and_exceptions() -> None:
    routine = _one(
        "CREATE PROCEDURE LOGISTICS.LOOP_SHIPMENTS(INTEGER) RETURNS INTEGER "
        "LANGUAGE NZPLSQL AS BEGIN_PROC "
        "DECLARE v CONSTANT INTEGER := 1; r RECORD; p ALIAS FOR $1; "
        "BEGIN IF p > 0 THEN RAISE NOTICE 'ok %', p; "
        "ELSIF p = 0 THEN EXIT WHEN p = 0; ELSE EXECUTE IMMEDIATE 'SELECT 1'; END IF; "
        "FOR i IN 1..3 LOOP CALL LOGISTICS.SHIP(p); END LOOP; "
        "FOR r IN SELECT ID FROM LOGISTICS.SHIPMENT LOOP "
        "UPDATE LOGISTICS.SHIPMENT SET STATUS = 'done' WHERE ID = r.ID; END LOOP; "
        "WHILE p > 0 LOOP p := p - 1; END LOOP; "
        "SELECT COUNT(*) INTO v FROM LOGISTICS.SHIPMENT; RETURN p; "
        "EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'failed'; END; END_PROC"
    )
    assert isinstance(routine, ast.CreateRoutine)
    assert routine.language == "NZPLSQL"
    assert routine.params[0].name.value == "$1"
    assert isinstance(routine.body, ast.Block)
    assert routine.body.declarations[0].name.value == "v"
    assert isinstance(routine.body.exception_handlers[0], ast.ExceptionHandler)
    branch = next(node for node in routine.body.statements if isinstance(node, ast.If))
    assert isinstance(branch.branches[0][1][0], ast.Raise)
    assert branch.branches[0][1][0].expr.args["level"] == "notice"
    assert branch.branches[0][1][0].expr.args["format"].value == "ok %"
    assert branch.branches[0][1][0].expr.args["args"][0].name.parts[0].value == "p"
    assert any(isinstance(node, ast.ForLoop) for node in routine.body.statements)
    assert any(isinstance(node, ast.While) for node in routine.body.statements)


def test_netezza_function_reftable_and_udx_definitions() -> None:
    function = _one(
        "CREATE FUNCTION LOGISTICS.ROW_SOURCE(INTEGER) RETURNS REFTABLE(LOGISTICS.SHIPMENT) "
        "EXECUTE AS CALLER LANGUAGE NZPLSQL AS 'SELECT 1'"
    )
    assert isinstance(function, ast.CreateRoutine)
    assert function.routine_kind == "function"
    assert function.options["returns_reftable"].parts[-1].value == "SHIPMENT"
    assert function.options["execute_as"] == "caller"
    library = _one("CREATE LIBRARY LOGISTICS.UDX_LIB '/opt/nz/udx.so'")
    aggregate = _one("CREATE AGGREGATE LOGISTICS.MEDIAN BASETYPE FLOAT")
    assert isinstance(library, ast.DialectStatement)
    assert library.args["object_type"] == "library"
    assert isinstance(aggregate, ast.DialectStatement)
    assert aggregate.args["object_type"] == "aggregate"


def test_netezza_user_redaction_groups_grants_and_schema() -> None:
    parser = Parser()
    result = parser.parse(
        "CREATE USER ETL WITH PASSWORD 'secret' IN GROUP OPS VALID UNTIL '2030-01-01'; "
        "CREATE GROUP OPS WITH USER ETL; "
        "ALTER GROUP OPS ADD USER ETL; "
        "GRANT CREATE TABLE, CREATE VIEW TO GROUP OPS; "
        "GRANT ALL ON TABLE TO OPS; "
        "REVOKE SELECT ON LOGISTICS.SHIPMENT FROM USER ETL; SET SCHEMA LOGISTICS"
    )
    serialized = json.dumps(result.to_dict())
    assert "secret" not in serialized
    assert "<redacted>" in serialized
    assert isinstance(result.statements[0], ast.CreatePrincipal)
    assert result.statements[0].options["in_group"][0].value == "OPS"
    assert isinstance(result.statements[2], ast.Alter)
    assert result.statements[2].actions[0].name == "add_user"
    assert result.statements[2].actions[0].args["member"].parts[0].value == "ETL"
    assert isinstance(result.statements[3], ast.Grant)
    assert result.statements[3].privileges == ["CREATE TABLE", "CREATE VIEW"]
    assert result.statements[4].object_name.parts == []
    assert isinstance(result.statements[5], ast.Revoke)
    assert isinstance(result.statements[-1], ast.Use)
    assert parser.default_schema == "LOGISTICS"


def test_netezza_sequence_casts_builtins_and_minus() -> None:
    sequence = _one(
        "CREATE SEQUENCE LOGISTICS.SHIP_SEQ AS BIGINT START WITH 1 INCREMENT BY 1 CYCLE"
    )
    assert isinstance(sequence, ast.CreateSequence)
    assert sequence.options["data_type"].name.parts[0].value == "BIGINT"
    query = _one(
        "SELECT NEXT VALUE FOR LOGISTICS.SHIP_SEQ, NOW(), DATE_TRUNC('day', ts), "
        "ISNULL(value, 0), CURRENT_TIMESTAMP, INTERVAL '1 day', "
        "value::VARCHAR(10)::DATE + 1 FROM LOGISTICS.SHIPMENT"
    )
    assert isinstance(query, ast.Query)
    assert query.body.projections[0].expr.name == "next_value"
    assert isinstance(query.body.projections[3].expr, ast.FunctionCall)
    assert isinstance(query.body.projections[4].expr, ast.ColumnRef)
    assert isinstance(query.body.projections[5].expr, ast.Interval)
    assert isinstance(query.body.projections[6].expr, ast.Binary)
    previous = _one("SELECT NEXTVAL FOR LOGISTICS.SHIP_SEQ, PREVIOUS VALUE FOR LOGISTICS.SHIP_SEQ")
    assert previous.body.projections[0].expr.name == "next_value"
    assert previous.body.projections[1].expr.name == "previous_value"
    minus = _one("SELECT ID FROM LOGISTICS.SHIPMENT MINUS SELECT ID FROM LOGISTICS.DELIVERED")
    assert isinstance(minus, ast.Query)
    assert isinstance(minus.body, ast.SetOperation)
    assert minus.body.op == "minus"


def test_netezza_references_and_maintenance_statements() -> None:
    groom = _one(
        "GROOM TABLE LOGISTICS.SHIPMENT RECORDS READY PAGES START VERSIONS "
        "RECLAIM BACKUPSET DEFAULT"
    )
    stats = _one("GENERATE EXPRESS STATISTICS ON LOGISTICS.SHIPMENT (ID)")
    assert isinstance(groom, ast.DialectStatement)
    assert groom.name == "groom_table"
    assert groom.args["records"] == "ready"
    assert groom.args["pages"] == "start"
    assert groom.args["versions"] is True
    assert any(ref.ref_kind == "writes" for ref in extract_references(groom, Parser))
    assert isinstance(stats, ast.DialectStatement)
    assert stats.args["express"] is True
    assert stats.args["columns"][0].value == "ID"
    assert any(ref.ref_kind == "reads" for ref in extract_references(stats, Parser))
    materialized = _one(
        "CREATE MATERIALIZED VIEW LOGISTICS.MV AS SELECT ID FROM LOGISTICS.SHIPMENT"
    )
    assert isinstance(materialized, ast.CreateView)
    assert materialized.materialized
    refresh = _one("REFRESH MATERIALIZED VIEW LOGISTICS.MV")
    assert isinstance(refresh, ast.DialectStatement)
    assert refresh.name == "refresh_materialized_view"
    assert any(ref.ref_kind == "writes" for ref in extract_references(refresh, Parser))


def test_netezza_object_types_include_external_and_materialized_objects() -> None:
    result = Parser().parse(
        "CREATE EXTERNAL TABLE LOGISTICS.STAGE (ID INT) USING (DATAOBJECT ('/stage.csv')); "
        "CREATE MATERIALIZED VIEW LOGISTICS.MV AS SELECT ID FROM LOGISTICS.STAGE"
    )
    objects = extract_objects(result, "objects.sql", Parser)
    assert [item.type for item in objects] == ["external_table", "materialized_view"]


def test_netezza_client_commands_lexer_and_crlf() -> None:
    result = Parser().parse(
        '\\c LOGISTICS ETL\n\\set ON_ERROR_STOP true\n\\echo "start"\n'
        '\\i /tmp/load.sql\nSELECT $1::INT FROM "LOGISTICS".T;\r\n'
    )
    assert isinstance(result.statements[0], ast.ClientCommand)
    assert isinstance(result.statements[1], ast.ClientCommand)
    assert isinstance(result.statements[2], ast.ClientCommand)
    assert isinstance(result.statements[3], ast.ClientCommand)
    assert isinstance(result.statements[4], ast.Query)
    assert not result.issues


def test_netezza_procedure_recovery_does_not_leak_body_tokens() -> None:
    result = Parser().parse(
        "CREATE PROCEDURE LOGISTICS.BAD() RETURNS INTEGER LANGUAGE NZPLSQL AS "
        "BEGIN_PROC BROKEN END_PROC; "
        "CREATE TABLE LOGISTICS.RECOVERED (ID INT)"
    )
    assert result.issues
    assert any(isinstance(node, ast.CreateTable) for node in result.statements)
    assert not any(
        isinstance(node, ast.Unparsed) and "END_PROC" in node.text for node in result.statements
    )
