from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery import ast
from migraai.discovery.objects import extract as extract_objects
from migraai.discovery.parsers.teradata import Parser
from migraai.discovery.references import extract as extract_references

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDENS = [
    (
        "select",
        "SEL id, amount + tax AS total FROM sales QUALIFY ROW_NUMBER() OVER (ORDER BY id) = 1",
    ),
    (
        "table",
        "CREATE MULTISET TABLE sales.orders "
        "(id INTEGER NOT NULL, amount DECIMAL(12,2) DEFAULT 0) PRIMARY INDEX (id)",
    ),
    ("ctas", "CREATE TABLE sales.order_copy AS (SELECT id FROM sales.orders) WITH NO DATA"),
    ("view", "REPLACE VIEW sales.order_view AS SELECT id FROM sales.orders"),
    ("grant", "GRANT SELECT ON sales.orders TO analyst WITH GRANT OPTION"),
    ("range_n", "SELECT RANGE_N(id BETWEEN 1 AND 100 EACH 10) FROM sales.orders"),
    ("sample", "SELECT TOP 10 id FROM sales.orders SAMPLE 10"),
    ("join_index", "CREATE JOIN INDEX sales.order_join AS SELECT o.id FROM sales.orders o"),
    ("hash_index", "CREATE HASH INDEX sales.order_hash (id) ON sales.orders"),
    ("statistics", "COLLECT STATISTICS COLUMN (id) ON sales.orders"),
    ("procedure", "CREATE PROCEDURE sales.p() BEGIN SET x = 1; END"),
    ("database", "DATABASE sales"),
]


@pytest.mark.parametrize(("name", "sql"), GOLDENS, ids=[name for name, _ in GOLDENS])
def test_teradata_ast_golden_json(name: str, sql: str) -> None:
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


def _one(sql: str) -> ast.Node:
    result = Parser().parse(sql)
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    return result.statements[0]


def test_teradata_expressions_precedence_and_special_forms() -> None:
    node = _one("SELECT a + b MOD c, a * b ** 2, 2 ** 3 ** 2, -x ** 2 FROM t")
    assert isinstance(node, ast.Query)
    projections = node.body.projections
    mod = projections[0].expr
    assert isinstance(mod, ast.Binary) and mod.op == "+"
    assert isinstance(mod.right, ast.Binary) and mod.right.op == "mod"
    power = projections[1].expr
    assert isinstance(power, ast.Binary) and power.op == "*"
    assert isinstance(power.right, ast.Binary) and power.right.op == "**"
    right_associative = projections[2].expr
    assert isinstance(right_associative, ast.Binary) and right_associative.op == "**"
    assert isinstance(right_associative.right, ast.Binary)
    assert isinstance(projections[3].expr, ast.Binary)
    forms = _one(
        "SELECT SUBSTRING(name FROM 2 FOR 3), TRIM(BOTH 'x' FROM name), "
        "POSITION('x' IN name) FROM t"
    )
    assert all(isinstance(item.expr, ast.FunctionCall) for item in forms.body.projections)
    assert isinstance(forms.body.projections[0].expr.args[1], ast.NamedArg)


def test_teradata_top_qualify_exec_macro_and_collect_statistics() -> None:
    top_query = _one(
        "SELECT TOP 5 PERCENT WITH TIES id FROM sales.orders "
        "QUALIFY ROW_NUMBER() OVER (ORDER BY id) = 1"
    )
    assert isinstance(top_query, ast.Query)
    assert isinstance(top_query.body.top, ast.Top)
    assert top_query.body.top.count.value == "5"
    assert top_query.body.top.percent is True
    assert top_query.body.top.with_ties is True
    assert isinstance(top_query.body.qualify, ast.Binary)
    assert top_query.body.qualify.op == "="

    macro = _one(
        "CREATE MACRO sales.set_one(p INTEGER DEFAULT 1) AS "
        "(INSERT INTO sales.orders (id) VALUES (p))"
    )
    assert isinstance(macro, ast.CreateRoutine)
    assert macro.routine_kind == "macro"
    executed = _one("EXEC sales.set_one(1)")
    assert isinstance(executed, ast.Call)

    statistics = _one("COLLECT STATISTICS USING SAMPLE 10 PERCENT COLUMN (id) ON sales.orders")
    assert isinstance(statistics, ast.DialectStatement)
    assert statistics.name == "collect_statistics"
    assert statistics.args["columns"][0][0].value == "id"
    assert statistics.args["using"]["sample"].value == "10"
    assert statistics.args["using"]["percent"] is True
    statistics_after_table = _one("COLLECT STATISTICS ON sales.orders COLUMN id")
    assert isinstance(statistics_after_table, ast.DialectStatement)
    assert statistics_after_table.args["columns"][0][0].value == "id"


def test_teradata_table_attributes_partitions_and_indexes_are_typed() -> None:
    table = _one(
        "CREATE SET VOLATILE TABLE sales.t (id BYTEINT FORMAT '99', "
        "code VARCHAR(10) CHARACTER SET LATIN NOT CASESPECIFIC, "
        "n INTEGER GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 2)) "
        "NO PRIMARY INDEX ON COMMIT PRESERVE ROWS"
    )
    assert isinstance(table, ast.CreateTable)
    assert table.options["set_multiset"] == "set"
    assert table.options["table_kind"] == "volatile"
    assert table.options["primary_index"] == "none"
    assert table.options["on_commit"] == "preserve"
    assert isinstance(table.columns[0].options["format"], ast.Literal)
    assert table.columns[1].options["character_set"] == "latin"
    assert isinstance(table.columns[2].identity, ast.DialectExpr)
    partitioned = _one(
        "CREATE TABLE sales.partitioned (id INTEGER) "
        "PARTITION BY (RANGE_N(id BETWEEN 1 AND 100 EACH 10), CASE_N(id WHEN 1 THEN 'a'))"
    )
    assert isinstance(partitioned, ast.CreateTable)
    assert [item.name for item in partitioned.options["partitioned_by"]] == [
        "range_n",
        "case_n",
    ]
    like_table = _one("CREATE TABLE sales.copy AS sales.source WITH NO DATA")
    assert isinstance(like_table, ast.CreateTable)
    assert like_table.like.parts[-1].value == "source"


def test_teradata_psm_declarations_handler_labels_and_recovery() -> None:
    routine = _one(
        "CREATE PROCEDURE sales.p() BEGIN "
        "DECLARE a, b INTEGER DEFAULT 0; "
        "DECLARE CONTINUE HANDLER FOR SQLSTATE '02000' SET a = 1; "
        "lbl: LOOP SET a = a + 1; ITERATE lbl; END LOOP lbl; "
        "SET b = 2; END"
    )
    assert isinstance(routine, ast.CreateRoutine)
    assert isinstance(routine.body, ast.Block)
    assert len([item for item in routine.body.declarations if isinstance(item, ast.Declare)]) == 2
    assert any(isinstance(item, ast.HandlerDecl) for item in routine.body.declarations)
    assert isinstance(routine.body.statements[0], ast.Loop)
    assert routine.body.statements[0].label == "lbl"
    broken = Parser().parse("CREATE PROCEDURE sales.bad() BEGIN SELECT FROM ; SET x = 1; END")
    assert broken.issues
    assert isinstance(broken.statements[0], ast.CreateRoutine)
    assert isinstance(broken.statements[0].body.statements[0], ast.Unparsed)
    assert isinstance(broken.statements[0].body.statements[1], ast.SetStatement)


def test_teradata_psm_cursor_diagnostics_signals_and_case_statement() -> None:
    routine = _one(
        "CREATE PROCEDURE sales.control_flow() BEGIN "
        "DECLARE missing CONDITION FOR SQLSTATE '02000'; "
        "DECLARE c CURSOR WITH RETURN TO CALLER FOR SELECT id FROM sales.t; "
        "GET DIAGNOSTICS row_count = ROW_COUNT; "
        "SIGNAL SQLSTATE VALUE '45000' SET MESSAGE_TEXT = 'stop'; "
        "RESIGNAL SQLSTATE '45001'; "
        "CASE status WHEN 'A' THEN SET status = 'B'; "
        "ELSE SET status = 'X'; END CASE; END"
    )
    assert isinstance(routine, ast.CreateRoutine)
    assert isinstance(routine.body, ast.Block)
    assert isinstance(routine.body.declarations[0], ast.Declare)
    cursor = routine.body.declarations[1]
    assert isinstance(cursor, ast.CursorDecl)
    assert cursor.options == {"with_return": True, "return_to": "caller"}
    assert routine.body.statements[0].name == "get_diagnostics"
    assert isinstance(routine.body.statements[1], ast.Signal)
    assert isinstance(routine.body.statements[2], ast.Signal)
    assert routine.body.statements[3].name == "case_statement"


def test_teradata_select_into_and_unstored_loop_labels() -> None:
    single = _one("SELECT id INTO sales.copy_target FROM sales.source")
    assert isinstance(single, ast.Query)
    assert single.body.into.parts[-1].value == "copy_target"

    multiple = _one("SELECT id INTO :first_target, :second_target FROM sales.source")
    assert isinstance(multiple, ast.DialectStatement)
    assert multiple.name == "select_into"
    assert [target.text for target in multiple.args["targets"]] == [
        ":first_target",
        ":second_target",
    ]
    assert isinstance(multiple.args["query"], ast.Query)

    routine = _one(
        "CREATE PROCEDURE sales.loops() BEGIN "
        "w: WHILE x < 3 DO SET x = x + 1; END WHILE w; "
        "r: REPEAT SET x = x - 1; UNTIL x = 0 END REPEAT r; "
        "f: FOR cursor_row AS cursor_name CURSOR FOR SELECT x FROM sales.t "
        "DO SET x = x + 1; END FOR f; "
        "END"
    )
    assert isinstance(routine, ast.CreateRoutine)
    assert isinstance(routine.body.statements[0], ast.While)
    assert isinstance(routine.body.statements[1], ast.Repeat)
    assert isinstance(routine.body.statements[2], ast.ForLoop)
    assert routine.body.statements[0].label is None
    assert routine.body.statements[1].label is None
    assert routine.body.statements[2].label is None
    assert routine.body.statements[2].cursor is None


def test_teradata_sampling_alias_dml_and_partition_forms() -> None:
    sampled = _one("SELECT id FROM sales.orders SAMPLE .25 WITH REPLACEMENT RANDOMIZED ALLOCATION")
    assert isinstance(sampled, ast.Query)
    assert isinstance(sampled.body.sample, ast.DialectExpr)
    assert sampled.body.sample.name == "sample"
    assert sampled.body.sample.args["with_replacement"] is True
    assert sampled.body.sample.args["randomized_allocation"] is True

    inserted = _one("INS INTO sales.orders (id) SEL 1")
    assert isinstance(inserted, ast.Insert)
    assert isinstance(inserted.source, ast.Query)
    updated = _one("UPD sales.orders FROM sales.source SET id = source.id WHERE id > 0")
    assert isinstance(updated, ast.Update)
    assert len(updated.from_) == 1
    deleted = _one("DEL sales.orders ALL")
    assert isinstance(deleted, ast.Delete)
    assert deleted.where is None
    merged = _one(
        "MERGE INTO sales.orders AS target USING sales.source AS src "
        "ON target.id = src.id WHEN MATCHED THEN UPDATE SET id = src.id "
        "WHEN NOT MATCHED THEN INSERT (id) VALUES (src.id)"
    )
    assert isinstance(merged, ast.Merge)

    range_query = _one(
        "SELECT RANGE_N(id BETWEEN * AND 100 EACH 10, "
        "BETWEEN 101 AND 200 EACH 10, NO RANGE OR UNKNOWN), "
        "CASE_N(id WHEN 1 THEN 'a', WHEN 2 THEN 'b', NO CASE OR UNKNOWN) FROM sales.orders"
    )
    range_node = range_query.body.projections[0].expr
    case_node = range_query.body.projections[1].expr
    assert isinstance(range_node, ast.DialectExpr) and range_node.name == "range_n"
    assert isinstance(range_node.args["ranges"][0].args["start"], ast.Star)
    assert len(range_node.args["ranges"]) == 2
    assert range_node.args["or_unknown"] is True
    assert isinstance(case_node, ast.DialectExpr) and case_node.name == "case_n"
    assert case_node.args["or_unknown"] is True


def test_teradata_data_types_cast_attributes_and_routines() -> None:
    table = _one(
        "CREATE TABLE sales.temporal_types ("
        "period_col PERIOD(DATE), "
        "duration INTERVAL DAY(4) TO SECOND(6), "
        "created TIMESTAMP(0) WITH TIME ZONE)"
    )
    assert isinstance(table, ast.CreateTable)
    assert table.columns[1].type.name.parts[0].value == "INTERVAL"
    assert table.columns[1].type.params[0] == "day"
    assert table.columns[1].type.params[2] == "to"
    assert table.columns[1].type.params[3] == "second"

    query = _one(
        "SELECT name (FORMAT 'X'), amount (DECIMAL(8,2), FORMAT '999'), "
        "amount (INTEGER), code (CASESPECIFIC), code NOT CASESPECIFIC, "
        "code UPPERCASE, CAST(amount AS DATE FORMAT 'YYYYMMDD'), "
        "day_value - INTERVAL '7' DAY, (ts2 - ts1) DAY(4) TO SECOND(6) FROM sales.t"
    )
    assert isinstance(query, ast.Query)
    assert isinstance(query.body.projections[0].expr, ast.DialectExpr)
    assert query.body.projections[0].expr.name == "td_attributes"
    assert isinstance(query.body.projections[6].expr, ast.Cast)
    assert isinstance(query.body.projections[7].expr.right, ast.Interval)
    assert isinstance(query.body.projections[8].expr, ast.DialectExpr)
    assert query.body.projections[8].expr.name == "interval_qualifier"

    function = _one(
        "CREATE FUNCTION sales.add_one(IN n INTEGER) RETURNS INTEGER "
        "LANGUAGE SQL DETERMINISTIC CONTAINS SQL SPECIFIC sales.add_one_v1 "
        "CALLED ON NULL INPUT SQL SECURITY DEFINER COLLATION INVOKER "
        "INLINE TYPE 1 RETURN n + 1"
    )
    assert isinstance(function, ast.CreateRoutine)
    assert function.options["sql_data_access"] == "contains"
    assert function.options["sql_security"] == "definer"
    assert isinstance(function.body, ast.Return)
    macro = _one(
        "CREATE MACRO sales.set_one(p INTEGER DEFAULT 1) AS (INSERT INTO sales.t (id) VALUES (p))"
    )
    assert isinstance(macro, ast.CreateRoutine)
    assert macro.routine_kind == "macro"
    execute = _one("EXECUTE sales.set_one(1)")
    assert isinstance(execute, ast.Call)
    abbreviated_execute = _one("EXEC sales.set_one(1)")
    assert isinstance(abbreviated_execute, ast.Call)


def test_teradata_table_options_principals_grants_and_view_locks() -> None:
    table = _one(
        "CREATE MULTISET TABLE sales.options, NO FALLBACK, NO BEFORE JOURNAL, "
        "CHECKSUM = DEFAULT, DEFAULT MERGEBLOCKRATIO, MAP = TD_MAP1, "
        "FREESPACE = 10 PERCENT, DATABLOCKSIZE = 1024 BYTES "
        "(id INTEGER) PRIMARY INDEX (id)"
    )
    assert isinstance(table, ast.CreateTable)
    assert len(table.options["table_options"]) == 7
    assert table.options["table_options"][1].args["name"] == "no_before_journal"
    principal = _one(
        "CREATE USER sales.writer FROM dbc AS PERM = 100, PASSWORD = 'unsafe-secret', "
        "SPOOL = 50 IN GROUP sales.reader"
    )
    assert isinstance(principal, ast.CreatePrincipal)
    assert principal.options["password"] == "<redacted>"
    assert "unsafe-secret" not in json.dumps(principal.to_dict())
    td_user = _one(
        "CREATE USER analyst FROM sales AS PERM = 0, PASSWORD = 'another-secret', "
        "DEFAULT DATABASE = sales"
    )
    assert isinstance(td_user, ast.CreatePrincipal)
    assert td_user.options["from"].parts[0].value == "sales"
    assert td_user.options["perm"].value == "0"
    assert td_user.options["default_database"].parts[0].value == "sales"
    assert td_user.options["password"] == "<redacted>"
    assert "another-secret" not in json.dumps(td_user.to_dict())
    database = _one("CREATE DATABASE sales_stage FROM dbc AS PERM = 100")
    assert isinstance(database, ast.CreateDatabase)
    assert database.options["from"].parts[0].value == "dbc"

    grant = _one("GRANT EXECUTE PROCEDURE ON sales.add_payment TO sales.operator")
    assert isinstance(grant, ast.Grant)
    assert grant.privileges == ["EXECUTE PROCEDURE"]
    role_grant = _one("GRANT reader TO analyst")
    assert isinstance(role_grant, ast.Grant)
    assert role_grant.privileges == ["ROLE"]
    assert role_grant.object_kind == "role"
    assert role_grant.object_name.parts[0].value == "reader"
    assert role_grant.grantees[0].parts[0].value == "analyst"
    view = _one("REPLACE VIEW sales.locked AS LOCKING ROW FOR ACCESS SEL id FROM sales.orders")
    assert isinstance(view, ast.CreateView)
    assert view.replace is True
    assert view.options["locking"][0].args["mode"] == "access"
    locking = _one("LOCKING ROW FOR ACCESS SEL id FROM sales.orders")
    assert isinstance(locking, ast.DialectStatement)
    assert locking.name == "locking"
    assert isinstance(locking.args["statement"], ast.Query)


def test_teradata_principals_are_not_scoped_by_database() -> None:
    result = Parser().parse("DATABASE sales; CREATE ROLE auditor; CREATE USER analyst FROM dbc")
    roles_and_users = [node for node in result.statements if isinstance(node, ast.CreatePrincipal)]
    assert [node.name.to_dict()["parts"][-1]["value"] for node in roles_and_users] == [
        "auditor",
        "analyst",
    ]
    assert all(len(node.name.parts) == 1 for node in roles_and_users)


def test_teradata_logon_redacts_all_suffix_text_and_tracks_schema() -> None:
    result = Parser().parse(".LOGON db/user,very_secret,$tdwallet(x) trailing\nDATABASE sales;")
    serialized = json.dumps(result.to_dict())
    assert "very_secret" not in serialized
    assert "$tdwallet" not in serialized
    assert "trailing" not in serialized
    assert ".LOGON db/user" in serialized
    constructed = Parser(".LOGON demo,constructor_secret").parse()
    assert "constructor_secret" not in json.dumps(constructed.to_dict())
    assert Parser().parse("DATABASE sales").statements[0].database.parts[0].value == "sales"
    assert Parser().parse("SET DATABASE sales").statements[0].database.parts[0].value == "sales"
    parser = Parser()
    parser.parse("DATABASE sales; SET DATABASE reporting;")
    assert parser.default_schema == "reporting"
    broken_before_logon = Parser().parse(
        "CREATE TABLE sales.broken (id);\nBROKEN SQL HERE;\n.LOGON db/user,hidden-password\n"
    )
    assert "hidden-password" not in json.dumps(broken_before_logon.to_dict())
    assert any(isinstance(node, ast.ClientCommand) for node in broken_before_logon.statements)


def test_teradata_top_level_error_recovery() -> None:
    result = Parser().parse(
        "CREATE TABLE sales.broken (id); "
        "CREATE TABLE sales.recovered (id INTEGER); "
        "CREATE AUTHORIZATION sales.unknown; "
        "CREATE TABLE sales.after_unsupported (id INTEGER)"
    )
    assert result.issues
    assert any(isinstance(item, ast.Unparsed) for item in result.statements)
    definitions = [
        item.name.parts[-1].value for item in result.statements if isinstance(item, ast.CreateTable)
    ]
    assert definitions == ["recovered", "after_unsupported"]


def test_teradata_references_and_outer_join_contract() -> None:
    statement = _one("CREATE HASH INDEX sales.ix (id) ON sales.orders; ")
    references = extract_references(statement, Parser)
    assert [(item.ref_kind, item.name.parts[-1].value) for item in references] == [
        ("index_on", "orders")
    ]
    objects = extract_objects(
        Parser().parse("CREATE HASH INDEX sales.ix (id) ON sales.orders"), "x.sql", Parser
    )
    assert objects[0].type == "hash_index"
    secondary = _one("CREATE UNIQUE INDEX sales.by_id (id) ON sales.orders")
    assert isinstance(secondary, ast.CreateIndex)
    join_index = _one(
        "CREATE JOIN INDEX sales.order_join AS SELECT o.id FROM sales.orders o "
        "JOIN sales.customer c ON o.customer_id = c.id"
    )
    assert isinstance(join_index, ast.DialectStatement)
    assert join_index.name == "create_join_index"
    assert isinstance(join_index.args["query"], ast.Query)
    for join_type in ("LEFT OUTER", "RIGHT OUTER", "FULL OUTER"):
        joined = _one(f"SELECT a.id FROM a {join_type} JOIN b ON a.id = b.id")
        assert isinstance(joined, ast.Query)
    invalid = Parser().parse("SELECT * FROM a, b WHERE a.id = b.id(+)")
    assert invalid.issues


def test_teradata_crlf_and_dot_commands() -> None:
    result = Parser().parse(".LOGON demo/user,secret\r\nSELECT 1;\r\n")
    assert isinstance(result.statements[0], ast.ClientCommand)
    assert "secret" not in json.dumps(result.to_dict())
    assert isinstance(result.statements[1], ast.Query)
    commands = Parser().parse("BT;\nET;\n.LOGOFF")
    assert commands.statements[0].name == "begin_transaction"
    assert isinstance(commands.statements[1], ast.Commit)
    assert isinstance(commands.statements[2], ast.ClientCommand)
