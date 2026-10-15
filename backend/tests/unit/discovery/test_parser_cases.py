from __future__ import annotations

import re

import pytest

from migraai.discovery.parsers.ansi import Parser

PathAssertion = tuple[str, object]
ParserCase = tuple[str, str, tuple[PathAssertion, ...]]


def _case(sql: str, kind: str, *assertions: PathAssertion) -> ParserCase:
    return sql, kind, assertions


CASES: list[ParserCase] = [
    _case("SELECT 1", "query", ("body.kind", "select")),
    _case("SELECT DISTINCT 1", "query", ("body.distinct", True)),
    _case("SELECT DISTINCT ON (id) id FROM t", "query", ("body.distinct_on[0].kind", "column_ref")),
    _case("SELECT TOP (5) id FROM t", "query", ("body.top.count.value", "5")),
    _case("SELECT id INTO out_table FROM t", "query", ("body.into.parts[0].value", "out_table")),
    _case("SELECT * FROM t", "query", ("body.from_[0].kind", "table_ref")),
    _case("SELECT * FROM t AS x", "query", ("body.from_[0].alias.value", "x")),
    _case("SELECT * FROM a CROSS JOIN b", "query", ("body.from_[0].join_type", "cross")),
    _case("SELECT * FROM (SELECT 1) AS q", "query", ("body.from_[0].kind", "subquery_ref")),
    _case("SELECT * FROM f(1) AS q", "query", ("body.from_[0].kind", "table_function_ref")),
    _case("VALUES (1), (2)", "query", ("body.kind", "values")),
    _case("SELECT 1 WHERE 1 = 1", "query", ("body.where.kind", "binary")),
    _case("SELECT 1 GROUP BY 1", "query", ("body.group_by.kind", "group_by")),
    _case("SELECT 1 HAVING COUNT(*) > 0", "query", ("body.having.kind", "binary")),
    _case("SELECT 1 QUALIFY 1 = 1", "query", ("body.qualify.kind", "binary")),
    _case("SELECT 1 ORDER BY 1 DESC", "query", ("order_by[0].desc", True)),
    _case("SELECT 1 LIMIT 2", "query", ("limit.count.value", "2")),
    _case("SELECT 1 OFFSET 2 ROWS FETCH NEXT 3 ROWS ONLY", "query", ("limit.fetch_first", True)),
    _case(
        "SELECT SUM(x) OVER w FROM t WINDOW w AS (PARTITION BY y)",
        "query",
        ("body.windows.w.kind", "window_spec"),
    ),
    _case(
        "WITH RECURSIVE c(n) AS (SELECT 1) SELECT n FROM c",
        "query",
        ("with_.recursive", True),
        ("body.kind", "select"),
    ),
    _case("SELECT 1 UNION ALL SELECT 2", "query", ("body.op", "union"), ("body.all", True)),
    _case("SELECT 1 EXCEPT SELECT 2", "query", ("body.op", "except")),
    _case("SELECT 1 INTERSECT SELECT 2", "query", ("body.op", "intersect")),
    _case(
        "SELECT * EXCEPT (a) FROM t", "query", ("body.projections[0].expr.except_[0].value", "a")
    ),
    _case(
        "SELECT * REPLACE (a + 1 AS a) FROM t",
        "query",
        ("body.projections[0].expr.replace[0].name.value", "a"),
    ),
    _case("SELECT * FROM t TABLESAMPLE SYSTEM (10)", "query", ("body.from_[0].sample.value", "10")),
    _case("SELECT -a + b", "query", ("body.projections[0].expr.left.kind", "unary")),
    _case(
        "SELECT NOT a = b AND c",
        "query",
        ("body.projections[0].expr.left.kind", "unary"),
        ("body.projections[0].expr.left.operand.kind", "binary"),
    ),
    _case(
        "SELECT a BETWEEN 1 AND 2 AND active",
        "query",
        ("body.projections[0].expr.left.kind", "between"),
        ("body.projections[0].expr.op", "and"),
    ),
    _case(
        "SELECT a IS DISTINCT FROM b", "query", ("body.projections[0].expr.op", "is_distinct_from")
    ),
    _case(
        "SELECT a IS NOT DISTINCT FROM b",
        "query",
        ("body.projections[0].expr.op", "is_not_distinct_from"),
    ),
    _case("SELECT 'x' || 1 + 2", "query", ("body.projections[0].expr.op", "||")),
    _case(
        "SELECT CASE WHEN x > 0 THEN CASE WHEN y > 0 THEN 1 ELSE 2 END ELSE 0 END",
        "query",
        ("body.projections[0].expr.whens[0].result.kind", "case"),
    ),
    _case(
        "SELECT x = ANY (SELECT y FROM t)",
        "query",
        ("body.projections[0].expr.kind", "binary"),
        ("body.projections[0].expr.right.kind", "quantified"),
    ),
    _case(
        "SELECT ROW_NUMBER() OVER (ORDER BY x ROWS BETWEEN 2 PRECEDING AND CURRENT ROW "
        "EXCLUDE TIES)",
        "query",
        ("body.projections[0].expr.over.frame.exclude", "ties"),
    ),
    _case(
        "SELECT x FROM t GROUP BY GROUPING SETS ((a), (b))",
        "query",
        ("body.group_by.grouping_sets[0].kind", "grouping_sets"),
    ),
    _case(
        "SELECT x FROM t GROUP BY ROLLUP (a)", "query", ("body.group_by.rollup[0].kind", "rollup")
    ),
    _case("SELECT x FROM t GROUP BY CUBE (a)", "query", ("body.group_by.cube[0].kind", "cube")),
    _case(
        "SELECT INTERVAL '1' DAY TO SECOND", "query", ("body.projections[0].expr.kind", "interval")
    ),
    _case("SELECT EXTRACT(YEAR FROM d)", "query", ("body.projections[0].expr.field", "YEAR")),
    _case("SELECT x COLLATE latin1", "query", ("body.projections[0].expr.kind", "collate")),
    _case("SELECT a[1].b[2]", "query", ("body.projections[0].expr.kind", "subscript")),
    _case("SELECT (a[1]).b", "query", ("body.projections[0].expr.kind", "field_access")),
    _case("SELECT x IN (1, 2)", "query", ("body.projections[0].expr.kind", "in_list")),
    _case(
        "SELECT x IN (SELECT y FROM t)", "query", ("body.projections[0].expr.kind", "in_subquery")
    ),
    _case("SELECT x NOT LIKE 'a%'", "query", ("body.projections[0].expr.negated", True)),
    _case("SELECT EXISTS (SELECT 1)", "query", ("body.projections[0].expr.kind", "exists")),
    _case("SELECT CAST(x AS INT)", "query", ("body.projections[0].expr.kind", "cast")),
    _case("SELECT x::INT", "query", ("body.projections[0].expr.style", "double_colon")),
    _case("SELECT ARRAY[1, 2]", "query", ("body.projections[0].expr.kind", "array_literal")),
    _case("SELECT STRUCT(1, 2)", "query", ("body.projections[0].expr.kind", "struct_literal")),
    _case("INSERT INTO t (id) VALUES (1)", "insert", ("source.kind", "values")),
    _case("INSERT INTO t SELECT id FROM s", "insert", ("source.body.kind", "select")),
    _case("INSERT OVERWRITE TABLE t VALUES (1)", "insert", ("overwrite", True)),
    _case("INSERT INTO t DEFAULT VALUES", "insert", ("source.kind", "default_values")),
    _case("UPDATE t SET id = 2 WHERE id = 1", "update", ("assignments[0].kind", "assignment")),
    _case("UPDATE t SET id = 2 FROM s", "update", ("from_[0].kind", "table_ref")),
    _case("DELETE FROM t WHERE id = 1", "delete", ("where.kind", "binary")),
    _case("DELETE FROM t USING s", "delete", ("using[0].kind", "table_ref")),
    _case(
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
        "merge",
        ("source.kind", "table_ref"),
    ),
    _case(
        "MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)",
        "merge",
        ("clauses[0].action.kind", "insert"),
    ),
    _case("TRUNCATE TABLE t", "truncate", ("target.parts[0].value", "t")),
    _case("CREATE TABLE t (id INT)", "create_table", ("columns[0].name.value", "id")),
    _case(
        "CREATE TABLE t (id INT PRIMARY KEY, parent INT REFERENCES p(id))",
        "create_table",
        ("columns[1].constraints[0].constraint_kind", "foreign_key"),
    ),
    _case(
        "CREATE TABLE t (id DECIMAL(10, 2) NOT NULL)",
        "create_table",
        ("columns[0].type.params[0].value", "10"),
    ),
    _case("CREATE TEMPORARY TABLE t (id INT)", "create_table", ("temporary", True)),
    _case("CREATE EXTERNAL TABLE t (id INT)", "create_table", ("external", True)),
    _case("CREATE TABLE t AS SELECT 1", "create_table", ("as_query.kind", "query")),
    _case("CREATE VIEW v AS SELECT 1", "create_view", ("query.kind", "query")),
    _case("CREATE OR REPLACE VIEW v AS SELECT 1", "create_view", ("replace", True)),
    _case("CREATE MATERIALIZED VIEW v AS SELECT 1", "create_view", ("materialized", True)),
    _case("CREATE SECURE VIEW v AS SELECT 1", "create_view", ("secure", True)),
    _case("CREATE UNIQUE INDEX ix ON t (id)", "create_index", ("unique", True)),
    _case(
        "CREATE SEQUENCE seq START WITH 1",
        "create_sequence",
        ("name.parts[0].value", "seq"),
        ("options.start_with.value", "1"),
    ),
    _case("CREATE SYNONYM s FOR t", "create_synonym", ("target.parts[0].value", "t")),
    _case("CREATE SCHEMA s", "create_schema", ("name.parts[0].value", "s")),
    _case("CREATE DATABASE d", "create_database", ("name.parts[0].value", "d")),
    _case("CREATE TYPE mood AS ENUM ('low', 'high')", "create_routine", ("routine_kind", "type")),
    _case("CREATE MACRO m AS (SELECT 1)", "create_routine", ("routine_kind", "macro")),
    _case(
        "CREATE PROCEDURE p() BEGIN CALL q(); END",
        "create_routine",
        ("body.kind", "block"),
        ("body.statements[0].kind", "call"),
    ),
    _case(
        "CREATE FUNCTION f(x INT) RETURNS INT RETURN x + 1",
        "create_routine",
        ("returns.kind", "data_type"),
    ),
    _case(
        "CREATE TRIGGER trg AFTER INSERT ON t FOR EACH ROW BEGIN SET x = 1; END",
        "create_routine",
        ("routine_kind", "trigger"),
        ("options.trigger_on.parts[0].value", "t"),
    ),
    _case("ALTER TABLE t ADD COLUMN x INT", "alter", ("actions[0].kind", "column_def")),
    _case("ALTER TABLE t DROP COLUMN x", "alter", ("actions[0].kind", "drop")),
    _case("ALTER TABLE t RENAME COLUMN x TO y", "alter", ("actions[0].kind", "rename")),
    _case("DROP TABLE IF EXISTS t CASCADE", "drop", ("if_exists", True), ("cascade", True)),
    _case("DROP VIEW v", "drop", ("object_kind", "view")),
    _case("COMMENT ON TABLE t IS 'table'", "comment_on", ("text", "table")),
    _case("RENAME TABLE t TO u", "rename", ("new_name.parts[0].value", "u")),
    _case("GRANT SELECT ON TABLE t TO analyst", "grant", ("privileges[0]", "SELECT")),
    _case("REVOKE SELECT ON TABLE t FROM analyst", "revoke", ("object_kind", "table")),
    _case("CREATE ROLE analyst", "create_principal", ("principal_kind", "role")),
    _case("CREATE USER app_user", "create_principal", ("principal_kind", "user")),
    _case("COPY t FROM 'data.csv'", "dialect_statement", ("name", "copy")),
    _case("LOAD DATA INPATH 'data.csv' INTO TABLE t", "dialect_statement", ("name", "load")),
    _case("BEGIN SET x = 1; END", "block", ("statements[0].kind", "set_statement")),
    _case("DECLARE x INT DEFAULT 1", "declare", ("name.value", "x")),
    _case("DECLARE c CURSOR FOR SELECT 1", "cursor_decl", ("query.body.kind", "select")),
    _case(
        "DECLARE CONTINUE HANDLER FOR NOT FOUND SET x = 1",
        "handler_decl",
        ("condition", "continue NOT FOUND"),
        ("statements[0].kind", "set_statement"),
    ),
    _case("IF x > 0 THEN SET y = 1; END IF", "if", ("branches[0][1][0].kind", "set_statement")),
    _case(
        "WHILE x < 2 DO SET x = x + 1; END WHILE", "while", ("statements[0].kind", "set_statement")
    ),
    _case("LOOP EXIT; END LOOP", "loop", ("statements[0].kind", "exit")),
    _case("FOR i IN 1..3 DO SET x = i; END FOR", "for_loop", ("iterable.kind", "range")),
    _case("FOR r IN SELECT 1 DO SET x = 1; END FOR", "for_loop", ("iterable.kind", "query")),
    _case("REPEAT SET x = x + 1; UNTIL x > 2 END REPEAT", "repeat", ("until.kind", "binary")),
    _case("LEAVE label", "leave", ("label", "label")),
    _case("EXIT label", "exit", ("label", "label")),
    _case("CONTINUE label", "continue", ("label", "label")),
    _case("RETURN 1", "return", ("expr.value", "1")),
    _case("RAISE 'error'", "raise", ("expr.value", "error")),
    _case("SIGNAL SQLSTATE", "signal", ("condition", "SQLSTATE")),
    _case("SET x = 1", "set_statement", ("items[0].kind", "set_item")),
    _case("CALL p(1)", "call", ("args[0].value", "1")),
    _case("EXECUTE IMMEDIATE 'SELECT 1'", "dynamic_sql", ("parsed[0].kind", "query")),
    _case("PREPARE stmt FROM 'SELECT 1'", "dynamic_sql", ("parsed[0].kind", "query")),
    _case("OPEN c", "open_cursor", ("name.value", "c")),
    _case("FETCH NEXT FROM c INTO x", "fetch", ("into[0].value", "x")),
    _case("CLOSE c", "close_cursor", ("name.value", "c")),
    _case("COMMIT", "commit", ("kind", "commit")),
    _case("ROLLBACK", "rollback", ("kind", "rollback")),
    _case("ROLLBACK TO sp", "rollback", ("savepoint.value", "sp")),
    _case("USE sales", "use", ("database.parts[0].value", "sales")),
]

for number in range(60):
    CASES.append(
        _case(
            f"SELECT {number} + {number + 1}",
            "query",
            ("body.projections[0].expr.kind", "binary"),
            ("body.projections[0].expr.left.value", str(number)),
        )
    )


def _resolve_path(node: object, path: str) -> object:
    current = node
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            current = current[int(part[1:-1])]
        elif isinstance(current, dict):
            current = current[part]
        else:
            current = getattr(current, part)
    return current


assert len(CASES) >= 150


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
def test_parser_case_tree(case: ParserCase) -> None:
    sql, expected_kind, assertions = case
    result = Parser().parse(sql, "case.sql")
    assert result.issues == [], (sql, result.issues)
    assert len(result.statements) == 1, (sql, result.statements)
    statement = result.statements[0]
    assert statement.kind == expected_kind
    assert 1 <= len(assertions) <= 3
    for path, expected in assertions:
        assert _resolve_path(statement, path) == expected, (sql, path)
