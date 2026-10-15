from __future__ import annotations

import json

from migraai.discovery import ast
from migraai.discovery.lexer import Lexer, TokenKind
from migraai.discovery.parsers.oracle import Parser
from migraai.discovery.parsers.oracle.lexer import LEXER_CONFIG


def _parse_one(sql: str) -> ast.Node:
    result = Parser().parse(sql)
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    return result.statements[0]


def test_oracle_lexer_q_quotes_comments_parameters_and_sqlplus_batches() -> None:
    lexer = Lexer(
        "SELECT q'[a ''quoted'' value]' AS txt, :customer_id, &prompt FROM dual;\n/\n",
        LEXER_CONFIG,
    )
    tokens = lexer.tokenize()
    assert not lexer.issues
    assert any(
        token.kind is TokenKind.STRING and token.value == "a ''quoted'' value" for token in tokens
    )
    assert any(token.kind is TokenKind.PARAM and token.text == ":customer_id" for token in tokens)
    assert any(token.kind is TokenKind.PARAM and token.text == "&prompt" for token in tokens)
    assert sum(token.kind is TokenKind.TERMINATOR for token in tokens) == 2

    result = Parser().parse(
        "SET SERVEROUTPUT ON\nCONNECT alice/private-password@dev\n"
        "DEFINE api_secret = 'private-token'\nSELECT q'{safe}' AS txt FROM dual;\n"
        "UPDATE oe.orders\nSET status = 'DONE' WHERE order_id = 1;\n"
        "EXEC oe.close_order(1);\n"
    )
    assert not result.issues, result.issues
    serialized = json.dumps([node.to_dict() for node in result.statements])
    assert "private-password" not in serialized
    assert "private-token" not in serialized
    assert "alice/***@dev" in serialized
    assert any(isinstance(node, ast.Update) for node in result.statements)
    assert any(isinstance(node, ast.Call) for node in result.statements)


def test_oracle_expression_precedence_and_case() -> None:
    query = _parse_one(
        "SELECT amount * 2 + 1 || suffix AS text_value, "
        "2 ** 3 ** 2 AS power_value, "
        "CASE WHEN amount > 0 THEN 'positive' ELSE 'zero' END AS label "
        "FROM oe.orders"
    )
    select = query.body
    assert isinstance(select, ast.Select)
    concat = select.projections[0].expr
    assert isinstance(concat, ast.Binary) and concat.op == "||"
    assert isinstance(concat.left, ast.Binary) and concat.left.op == "+"
    assert isinstance(concat.left.left, ast.Binary) and concat.left.left.op == "*"
    power = select.projections[1].expr
    assert isinstance(power, ast.Binary) and power.op == "**"
    assert isinstance(power.right, ast.Binary) and power.right.op == "**"
    assert isinstance(select.projections[2].expr, ast.Case)


def test_oracle_hierarchical_query_pseudocolumns_and_outer_join_marker() -> None:
    query = _parse_one(
        "SELECT LEVEL, CONNECT_BY_ISLEAF, e.employee_id, d.department_name "
        "FROM oe.employees e, oe.departments d "
        "WHERE e.department_id = d.department_id(+) "
        "START WITH e.manager_id IS NULL CONNECT BY NOCYCLE PRIOR e.employee_id = e.manager_id"
    )
    select = query.body
    assert isinstance(select, ast.Select)
    assert select.hierarchical.name == "connect_by"
    assert select.hierarchical.args["nocycle"].value == "true"
    assert select.projections[0].expr.name == "pseudo_column"
    assert select.projections[1].expr.args["name"] == "CONNECT_BY_ISLEAF"
    assert select.where.right.name == "outer_join_marker"
    assert select.hierarchical.args["condition"].left.name == "prior"

    reverse_order = _parse_one(
        "SELECT LEVEL FROM oe.employees "
        "CONNECT BY NOCYCLE PRIOR employee_id = manager_id "
        "START WITH manager_id IS NULL"
    )
    assert reverse_order.body.hierarchical.args["start_with"] is not None
    assert reverse_order.body.hierarchical.args["nocycle"].value == "true"


def test_oracle_sequence_values_and_cursor_attributes() -> None:
    sequence = _parse_one("SELECT order_seq.NEXTVAL FROM dual")
    assert sequence.body.projections[0].expr.name == "sequence_value"
    cursor = _parse_one("SELECT SQL%ROWCOUNT FROM dual")
    assert cursor.body.projections[0].expr.name == "cursor_attribute"


def test_oracle_table_types_constraints_and_partitioning() -> None:
    table = _parse_one(
        "CREATE GLOBAL TEMPORARY TABLE oe.stage_orders ("
        "order_id NUMBER(12) PRIMARY KEY, "
        "created_at DATE DEFAULT DATE '2024-01-01' NOT NULL, "
        "status VARCHAR2(20 CHAR) DEFAULT 'NEW', "
        "customer_id NUMBER REFERENCES oe.customers(customer_id)) "
        "ON COMMIT PRESERVE ROWS "
        "PARTITION BY RANGE (created_at) ("
        "PARTITION p_current VALUES LESS THAN (DATE '2025-01-01'))"
    )
    assert isinstance(table, ast.CreateTable)
    assert table.temporary
    assert table.columns[2].type.params[1] == "char"
    assert table.columns[1].default.value == "2024-01-01"
    assert table.columns[3].constraints[0].ref_table.parts[-1].value == "customers"
    assert table.options["partition_by"].args["method"] == "range"
    assert table.options["on_commit"] == "preserve"

    anchored = _parse_one("DECLARE v oe.orders.order_id%TYPE; BEGIN NULL; END")
    assert isinstance(anchored, ast.Block)
    assert anchored.declarations[0].type.name == "TYPE"


def test_oracle_routine_declarations_control_flow_and_exception_handler() -> None:
    routine = _parse_one(
        "CREATE OR REPLACE FUNCTION oe.square_value(p_value IN NUMBER) RETURN NUMBER "
        "IS v_result NUMBER := p_value ** 2; BEGIN "
        "IF p_value > 0 THEN RETURN v_result; ELSIF p_value = 0 THEN RETURN 0; "
        "ELSE RETURN -1; END IF; "
        "EXCEPTION WHEN OTHERS THEN RAISE; END square_value"
    )
    assert isinstance(routine, ast.CreateRoutine)
    assert routine.routine_kind == "function"
    assert routine.params[0].mode == "in"
    assert routine.returns.name.parts[-1].value == "NUMBER"
    assert routine.body.declarations[0].default.op == "**"
    assert len(routine.body.statements[0].branches) == 2
    assert routine.body.exception_handlers[0].conditions == ["OTHERS"]


def test_oracle_dynamic_sql_bulk_fetch_and_forall() -> None:
    dynamic = _parse_one(
        "EXECUTE IMMEDIATE 'UPDATE oe.orders SET status = :1 WHERE order_id = :2' "
        "USING IN :new_status, IN order_id"
    )
    assert isinstance(dynamic, ast.DynamicSql)
    assert len(dynamic.parsed) == 1 and isinstance(dynamic.parsed[0], ast.Update)
    assert [argument.args["mode"] for argument in dynamic.using] == ["in", "in"]

    fetch = _parse_one("FETCH order_cursor BULK COLLECT INTO order_ids LIMIT 100")
    assert isinstance(fetch, ast.Fetch)
    assert fetch.bulk_collect and fetch.limit.value == "100"

    forall = _parse_one(
        "FORALL i IN 1..order_ids.COUNT SAVE EXCEPTIONS "
        "INSERT INTO oe.orders (order_id) VALUES (order_ids(i))"
    )
    assert forall.name == "forall"
    assert forall.args["save_exceptions"]
    assert isinstance(forall.args["statement"], ast.Insert)


def test_oracle_merge_preserves_matched_and_not_matched_actions() -> None:
    merge = _parse_one(
        "MERGE INTO oe.orders target USING oe.stage_orders source "
        "ON (target.order_id = source.order_id) "
        "WHEN MATCHED THEN UPDATE SET target.status = source.status "
        "WHERE source.status <> 'X' DELETE WHERE target.status = 'OLD' "
        "WHEN NOT MATCHED THEN INSERT (order_id, status) "
        "VALUES (source.order_id, source.status) WHERE source.status IS NOT NULL"
    )
    assert isinstance(merge, ast.Merge)
    assert merge.clauses[0].matched
    assert merge.clauses[0].action.name == "oracle_merge_update"
    assert isinstance(merge.clauses[0].action.args["delete_where"], ast.Binary)
    assert not merge.clauses[1].matched
    assert merge.clauses[1].action.name == "oracle_merge_insert"


def test_oracle_pivot_and_unpivot_are_typed_expressions() -> None:
    pivot = _parse_one(
        "SELECT * FROM oe.sales PIVOT (SUM(amount) FOR quarter IN (1 AS q1, 2 AS q2)) p"
    )
    pivot_source = pivot.body.from_[0]
    assert isinstance(pivot_source, ast.DialectExpr)
    assert pivot_source.name == "pivot"
    assert pivot_source.args["source"].name.parts[-1].value == "sales"

    unpivot = _parse_one(
        "SELECT * FROM oe.monthly UNPIVOT INCLUDE NULLS (amount FOR month_name IN (jan, feb))"
    )
    assert unpivot.body.from_[0].name == "unpivot"
    assert unpivot.body.from_[0].args["include_nulls"].value == "true"


def test_oracle_package_trigger_and_database_link_secrets() -> None:
    package = _parse_one(
        "CREATE OR REPLACE PACKAGE oe.order_api AS "
        "PROCEDURE submit_order(p_id IN NUMBER); END order_api"
    )
    assert package.routine_kind == "package"
    assert package.body.declarations[0].options["forward"]

    trigger = _parse_one(
        "CREATE OR REPLACE TRIGGER oe.order_audit "
        "AFTER INSERT OR UPDATE OF status ON oe.orders FOR EACH ROW "
        "BEGIN audit_pkg.record_change(:NEW.order_id); END order_audit"
    )
    assert trigger.routine_kind == "trigger"
    assert trigger.options["timing"] == "after"
    assert trigger.options["for_each_row"]
    assert [event["event"] for event in trigger.options["events"]] == ["insert", "update"]
    assert trigger.options["events"][1]["columns"][0].value == "status"

    link = _parse_one(
        "CREATE DATABASE LINK reporting_link CONNECT TO report_user "
        "IDENTIFIED BY 'private-password' USING 'REPORTING'"
    )
    assert "private-password" not in json.dumps(link.to_dict())
    assert link.args["options"]["password"] == "***"


def test_oracle_plsql_controls_cursors_and_labeled_loops() -> None:
    block = _parse_one(
        "DECLARE CURSOR order_cursor IS SELECT order_id FROM oe.orders; "
        "v_order_id NUMBER := 0; BEGIN "
        "OPEN order_cursor; FETCH order_cursor INTO v_order_id; CLOSE order_cursor; "
        "WHILE v_order_id < 2 LOOP v_order_id := v_order_id + 1; END LOOP; "
        "FOR i IN REVERSE 1..3 LOOP CONTINUE WHEN i = 2; END LOOP; "
        "<<finish_loop>> LOOP EXIT finish_loop WHEN v_order_id = 3; END LOOP finish_loop; "
        "CASE v_order_id WHEN 1 THEN NULL; ELSE NULL; END CASE; "
        "SAVEPOINT before_commit; COMMIT; ROLLBACK TO before_commit; END"
    )
    assert isinstance(block, ast.Block)
    assert isinstance(block.declarations[0], ast.CursorDecl)
    assert isinstance(block.statements[0], ast.OpenCursor)
    assert isinstance(block.statements[1], ast.Fetch)
    assert isinstance(block.statements[2], ast.CloseCursor)
    assert isinstance(block.statements[3], ast.While)
    assert isinstance(block.statements[4], ast.ForLoop)
    assert isinstance(block.statements[5], ast.Loop)
    assert block.statements[5].label == "finish_loop"
    assert block.statements[6].name == "case_statement"


def test_oracle_routine_recovers_from_invalid_inner_statement() -> None:
    result = Parser().parse("CREATE PROCEDURE oe.recovering IS BEGIN unsupported syntax; NULL; END")
    assert result.issues
    routine = result.statements[0]
    assert isinstance(routine, ast.CreateRoutine)
    assert isinstance(routine.body.statements[0], ast.Unparsed)
    assert routine.body.statements[1].name == "null_statement"


def test_oracle_recovery_preserves_following_statements() -> None:
    result = Parser().parse(
        "CREATE TABLE oe.broken (id NUMBER, CONSTRAINT broken_fk FOREIGN KEY (id) REFERENCES ); "
        "CREATE XML SCHEMA COLLECTION unsupported; "
        "CREATE TABLE oe.recovered (id NUMBER)"
    )
    assert any(isinstance(node, ast.Unparsed) for node in result.statements)
    assert isinstance(result.statements[-1], ast.CreateTable)
    assert result.statements[-1].name.parts[-1].value == "recovered"
