from __future__ import annotations

from migraai.discovery import ast
from migraai.discovery.lexer import Lexer, TokenKind
from migraai.discovery.parsers.oracle import Parser
from migraai.discovery.parsers.oracle.lexer import LEXER_CONFIG
from migraai.discovery.pipeline import discover_files
from migraai.discovery.references import extract as extract_references


def _parse_one(sql: str) -> ast.Node:
    result = Parser().parse(sql)
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    node = result.statements[0]
    assert all(child.kind != "unparsed" for child in node.walk())
    return node


def test_oracle_sqlplus_client_commands_and_slash_terminator() -> None:
    sql = "CONNECT scott/tiger@db\nSET SERVEROUTPUT ON\nSELECT 1 FROM dual;\n/\n"
    result = Parser().parse(sql)
    assert not result.issues, result.issues
    assert all(child.kind != "unparsed" for root in result.statements for child in root.walk())
    commands = [node for node in result.statements if isinstance(node, ast.ClientCommand)]
    assert len(commands) == 2
    assert all("tiger" not in node.text for node in commands)
    tokens = Lexer(sql, LEXER_CONFIG).tokenize()
    assert any(token.kind is TokenKind.TERMINATOR and token.text == "/" for token in tokens)


def test_oracle_package_spec_and_body_defined_in_edge() -> None:
    spec_sql = "CREATE OR REPLACE PACKAGE p AS PROCEDURE a; END p"
    body_sql = "CREATE OR REPLACE PACKAGE BODY p AS PROCEDURE a IS BEGIN NULL; END a; END p"
    spec = _parse_one(spec_sql)
    body = _parse_one(body_sql)
    assert isinstance(spec, ast.CreateRoutine) and spec.routine_kind == "package"
    assert isinstance(body, ast.CreateRoutine) and body.routine_kind == "package_body"
    objects, edges = discover_files(
        {"p.sql": spec_sql.encode(), "p_body.sql": body_sql.encode()}, "oracle"
    )
    assert [item.type for item in objects] == ["package", "package_body"]
    assert any(
        edge.kind == "defined_in"
        and edge.source == "p"
        and edge.target == "p"
        and not edge.external
        for edge in edges
    )


def test_oracle_procedure() -> None:
    procedure = _parse_one("CREATE OR REPLACE PROCEDURE p IS BEGIN NULL; END p")
    assert isinstance(procedure, ast.CreateRoutine)
    assert procedure.routine_kind == "procedure"
    assert isinstance(procedure.body, ast.Block)


def test_oracle_function() -> None:
    function = _parse_one(
        "CREATE OR REPLACE FUNCTION f(x NUMBER) RETURN NUMBER IS BEGIN RETURN x; END f"
    )
    assert isinstance(function, ast.CreateRoutine)
    assert function.routine_kind == "function"
    assert function.returns.name.parts[-1].value == "NUMBER"


def test_oracle_before_trigger() -> None:
    trigger = _parse_one("CREATE OR REPLACE TRIGGER t BEFORE INSERT ON base_t BEGIN NULL; END")
    assert isinstance(trigger, ast.CreateRoutine)
    assert trigger.options["timing"] == "before"
    assert trigger.options["events"][0]["event"] == "insert"


def test_oracle_after_trigger() -> None:
    trigger = _parse_one("CREATE OR REPLACE TRIGGER t AFTER UPDATE ON base_t BEGIN NULL; END")
    assert isinstance(trigger, ast.CreateRoutine)
    assert trigger.options["timing"] == "after"
    assert trigger.options["events"][0]["event"] == "update"


def test_oracle_instead_of_trigger() -> None:
    trigger = _parse_one(
        "CREATE OR REPLACE TRIGGER t INSTEAD OF UPDATE ON v FOR EACH ROW BEGIN NULL; END"
    )
    assert isinstance(trigger, ast.CreateRoutine)
    assert trigger.options["timing"] == "instead_of"
    assert trigger.options["for_each_row"] is True
    refs = extract_references(trigger, Parser)
    assert any(ref.ref_kind == "trigger_on" and ref.name.parts[-1].value == "v" for ref in refs)


def test_oracle_row_trigger_new_and_old_pseudorecords() -> None:
    trigger = _parse_one(
        "CREATE OR REPLACE TRIGGER t BEFORE UPDATE ON base_t FOR EACH ROW "
        "BEGIN UPDATE base_t SET x = :NEW.x WHERE id = :OLD.id; END"
    )
    assert trigger.options["for_each_row"] is True
    params = {node.text for node in trigger.walk() if isinstance(node, ast.Param)}
    assert {":NEW", ":OLD"} <= params


def test_oracle_anonymous_block() -> None:
    block = _parse_one("BEGIN NULL; END")
    assert isinstance(block, ast.Block)
    assert block.statements[0].name == "null_statement"


def test_oracle_cursor_declaration_and_operations() -> None:
    block = _parse_one(
        "DECLARE CURSOR c IS SELECT id FROM t; x NUMBER; BEGIN OPEN c; FETCH c INTO x; CLOSE c; END"
    )
    assert isinstance(block, ast.Block)
    cursor = next(item for item in block.declarations if isinstance(item, ast.CursorDecl))
    assert cursor.name.value == "c" and isinstance(cursor.query, ast.Query)
    assert any(isinstance(item, ast.OpenCursor) for item in block.statements)
    assert any(item.kind == "fetch" for item in block.statements)
    assert any(isinstance(item, ast.CloseCursor) for item in block.statements)


def test_oracle_for_loop() -> None:
    block = _parse_one("BEGIN FOR i IN 1..3 LOOP NULL; END LOOP; END")
    loop = next(node for node in block.walk() if isinstance(node, ast.ForLoop))
    assert loop.var.value == "i"
    assert isinstance(loop.iterable, ast.Range)


def test_oracle_while_loop() -> None:
    block = _parse_one("BEGIN WHILE i < 3 LOOP NULL; END LOOP; END")
    loop = next(node for node in block.walk() if isinstance(node, ast.While))
    assert loop.condition is not None and loop.statements


def test_oracle_exception_when_handler() -> None:
    block = _parse_one("BEGIN NULL; EXCEPTION WHEN NO_DATA_FOUND THEN NULL; END")
    assert isinstance(block, ast.Block)
    assert block.exception_handlers[0].conditions == ["NO_DATA_FOUND"]


def test_oracle_raise_application_error() -> None:
    block = _parse_one("BEGIN RAISE_APPLICATION_ERROR(-20001, 'failed'); END")
    call = next(node for node in block.walk() if isinstance(node, ast.Call))
    assert call.name.parts[-1].value == "RAISE_APPLICATION_ERROR"
    assert len(call.args) == 2


def test_oracle_execute_immediate_parses_literal_sql() -> None:
    dynamic = _parse_one("BEGIN EXECUTE IMMEDIATE 'DELETE FROM oe.t WHERE id = 1'; END")
    statement = next(node for node in dynamic.walk() if isinstance(node, ast.DynamicSql))
    assert isinstance(statement.parsed[0], ast.Delete)
    assert statement.parsed[0].target.parts[-1].value == "t"


def test_oracle_bulk_collect() -> None:
    block = _parse_one("BEGIN SELECT id BULK COLLECT INTO ids FROM t; END")
    select = next(node for node in block.walk() if isinstance(node, ast.Select))
    assert select.bulk_collect is True
    assert select.into_list[0].name.parts[-1].value == "ids"


def test_oracle_forall() -> None:
    block = _parse_one("BEGIN FORALL i IN 1..n SAVE EXCEPTIONS INSERT INTO t(id) VALUES (i); END")
    forall = next(node for node in block.walk() if isinstance(node, ast.DialectStatement))
    assert forall.name == "forall"
    assert forall.args["save_exceptions"] is True
    assert isinstance(forall.args["statement"], ast.Insert)


def test_oracle_percent_type() -> None:
    block = _parse_one("DECLARE v t.c%TYPE; BEGIN NULL; END")
    declaration = next(item for item in block.declarations if isinstance(item, ast.Declare))
    assert isinstance(declaration.type, ast.DataType)
    assert declaration.type.name == "TYPE"


def test_oracle_percent_rowtype() -> None:
    block = _parse_one("DECLARE r emp%ROWTYPE; BEGIN NULL; END")
    declaration = next(item for item in block.declarations if isinstance(item, ast.Declare))
    assert isinstance(declaration.type, ast.DataType)
    assert declaration.type.name == "ROWTYPE"


def test_oracle_plus_outer_join() -> None:
    query = _parse_one("SELECT a.id FROM a, b WHERE a.id = b.id(+)")
    marker = next(
        node
        for node in query.walk()
        if isinstance(node, ast.DialectExpr) and node.name == "outer_join_marker"
    )
    assert isinstance(marker.args["expr"], ast.ColumnRef)


def test_oracle_connect_by_start_with_prior() -> None:
    query = _parse_one(
        "SELECT LEVEL FROM emp START WITH manager_id IS NULL "
        "CONNECT BY PRIOR employee_id = manager_id"
    )
    hierarchy = query.body.hierarchical
    assert isinstance(hierarchy, ast.DialectExpr)
    assert hierarchy.name == "connect_by"
    assert hierarchy.args["start_with"] is not None
    assert any(isinstance(node, ast.DialectExpr) and node.name == "prior" for node in query.walk())


def test_oracle_merge() -> None:
    merge = _parse_one(
        "MERGE INTO t USING s ON (t.id = s.id) "
        "WHEN MATCHED THEN UPDATE SET t.v = s.v "
        "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v)"
    )
    assert isinstance(merge, ast.Merge)
    assert len(merge.clauses) == 2


def test_oracle_materialized_view() -> None:
    view = _parse_one("CREATE MATERIALIZED VIEW mv AS SELECT id FROM t")
    assert isinstance(view, ast.CreateView)
    assert view.materialized is True


def test_oracle_sequence_nextval() -> None:
    sequence = _parse_one("CREATE SEQUENCE s START WITH 1")
    query = _parse_one("SELECT s.NEXTVAL FROM dual")
    value = query.body.projections[0].expr
    assert isinstance(sequence, ast.CreateSequence)
    assert isinstance(value, ast.DialectExpr) and value.name == "sequence_value"
    assert value.args["sequence"].parts[-1].value == "s"


def test_oracle_private_synonym_reference() -> None:
    synonym = _parse_one("CREATE SYNONYM local_t FOR oe.remote_t")
    refs = extract_references(synonym, Parser)
    assert not synonym.public
    assert [(ref.ref_kind, ref.name.parts[-1].value) for ref in refs] == [
        ("synonym_for", "remote_t")
    ]


def test_oracle_public_synonym_reference() -> None:
    synonym = _parse_one("CREATE PUBLIC SYNONYM shared_t FOR oe.remote_t")
    refs = extract_references(synonym, Parser)
    assert synonym.public
    assert any(
        ref.ref_kind == "synonym_for" and ref.name.parts[-1].value == "remote_t" for ref in refs
    )


def test_oracle_database_link_reference() -> None:
    query = _parse_one("SELECT id FROM oe.orders@reporting_link")
    reference = next(ref for ref in extract_references(query, Parser) if ref.ref_kind == "reads")
    assert [part.value for part in reference.name.parts] == ["oe", "orders@reporting_link"]


def test_oracle_q_quoted_string() -> None:
    query = _parse_one("SELECT q'{safe text}' FROM dual")
    literal = query.body.projections[0].expr
    assert isinstance(literal, ast.Literal)
    assert literal.value == "safe text"


def test_oracle_q_bracket_quoted_string() -> None:
    query = _parse_one("SELECT q'[bracket text]' FROM dual")
    literal = query.body.projections[0].expr
    assert isinstance(literal, ast.Literal)
    assert literal.value == "bracket text"


def test_oracle_decode_function() -> None:
    query = _parse_one("SELECT DECODE(status, 1, 'yes', 'no') FROM t")
    function = query.body.projections[0].expr
    assert isinstance(function, ast.FunctionCall)
    assert function.name.parts[-1].value.casefold() == "decode"


def test_oracle_nvl_function() -> None:
    query = _parse_one("SELECT NVL(amount, 0) FROM t")
    function = query.body.projections[0].expr
    assert isinstance(function, ast.FunctionCall)
    assert function.name.parts[-1].value.casefold() == "nvl"


def test_oracle_rownum() -> None:
    query = _parse_one("SELECT id FROM t WHERE ROWNUM < 5")
    assert any(
        isinstance(node, ast.DialectExpr) and node.name == "pseudo_column" for node in query.walk()
    )


def test_oracle_pivot() -> None:
    query = _parse_one(
        "SELECT * FROM (SELECT dept, job, sal FROM emp) PIVOT (SUM(sal) FOR job IN ('A' AS a))"
    )
    pivot = next(node for node in query.walk() if isinstance(node, ast.DialectExpr))
    assert pivot.name == "pivot"
    assert pivot.args["for"]


def test_oracle_unpivot() -> None:
    query = _parse_one("SELECT * FROM t UNPIVOT (v FOR k IN (c1, c2))")
    unpivot = next(node for node in query.walk() if isinstance(node, ast.DialectExpr))
    assert unpivot.name == "unpivot"
    assert len(unpivot.args["for"]) == 1
