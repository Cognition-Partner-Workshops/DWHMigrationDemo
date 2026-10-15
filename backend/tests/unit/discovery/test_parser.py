from migraai.discovery import ast
from migraai.discovery.parser_base import ParserBase
from migraai.discovery.parsers.ansi import Parser


def _statement(sql: str) -> ast.Node:
    result = Parser().parse(sql, "test.sql")
    assert result.statements
    return result.statements[0]


def _projection(sql: str) -> ast.Node:
    statement = _statement(sql)
    assert isinstance(statement, ast.Query)
    assert isinstance(statement.body, ast.Select)
    return statement.body.projections[0].expr


def test_pratt_precedence_and_associativity() -> None:
    expression = _projection("SELECT 1 + 2 * 3 - 4 - 5")
    assert isinstance(expression, ast.Binary) and expression.op == "-"
    assert isinstance(expression.left, ast.Binary) and expression.left.op == "-"
    assert isinstance(expression.left.left, ast.Binary) and expression.left.left.op == "+"
    assert isinstance(expression.left.left.right, ast.Binary)
    assert expression.left.left.right.op == "*"

    logical = _projection("SELECT 1 OR 2 AND 3")
    assert isinstance(logical, ast.Binary) and logical.op == "or"
    assert isinstance(logical.right, ast.Binary) and logical.right.op == "and"

    power = _projection("SELECT 2 ^ 3 ^ 4")
    assert isinstance(power, ast.Binary) and power.op == "^"
    assert isinstance(power.right, ast.Binary) and power.right.op == "^"


def test_query_cte_sets_join_grouping_window_qualify_and_limits() -> None:
    statement = _statement(
        "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < 4) "
        "SELECT c.n, ROW_NUMBER() OVER (PARTITION BY c.n ORDER BY c.n "
        "ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS rn "
        "FROM c LEFT JOIN dim d ON c.n = d.n "
        "GROUP BY ROLLUP(c.n), CUBE(d.n), GROUPING SETS ((c.n), (d.n)) "
        "HAVING COUNT(*) > 0 QUALIFY rn = 1 ORDER BY c.n DESC "
        "OFFSET 1 ROWS FETCH NEXT 3 ROWS WITH TIES"
    )
    assert isinstance(statement, ast.Query)
    assert statement.with_ and statement.with_.recursive
    assert isinstance(statement.body, ast.Select)
    select = statement.body
    assert isinstance(select.from_[0], ast.Join)
    assert select.from_[0].join_type == "left"
    assert select.group_by and select.group_by.rollup and select.group_by.cube
    assert select.group_by.grouping_sets
    window_call = select.projections[1].expr
    assert isinstance(window_call, ast.FunctionCall) and window_call.over
    assert window_call.over.frame
    assert isinstance(window_call.over.frame.start, ast.FrameBound)
    assert window_call.over.frame.start.bound_type == "preceding"
    assert select.qualify is not None
    assert statement.limit and statement.limit.fetch_first and statement.limit.with_ties
    assert statement.order_by

    union = _statement("SELECT 1 UNION SELECT 2 INTERSECT ALL SELECT 3")
    assert isinstance(union, ast.Query)
    assert isinstance(union.body, ast.SetOperation)
    assert union.body.op == "union"
    assert isinstance(union.body.right.body, ast.SetOperation)
    assert union.body.right.body.op == "intersect"


def test_join_variants_and_from_item_extensions() -> None:
    for join in (
        "INNER JOIN",
        "LEFT OUTER JOIN",
        "RIGHT JOIN",
        "FULL JOIN",
        "CROSS JOIN",
        "NATURAL JOIN",
        "LEFT SEMI JOIN",
        "LEFT ANTI JOIN",
    ):
        node = _statement(f"SELECT * FROM a {join} b ON a.id = b.id")
        assert isinstance(node, ast.Query), join
        assert isinstance(node.body, ast.Select), join
        assert isinstance(node.body.from_[0], ast.Join), join

    temporal = _statement(
        "SELECT * FROM accounts FOR SYSTEM_TIME AS OF TIMESTAMP '2025-01-01' "
        "TABLESAMPLE SYSTEM (10) WITH (NOLOCK)"
    )
    assert isinstance(temporal, ast.Query)
    table = temporal.body.from_[0]
    assert isinstance(table, ast.TableRef)
    assert table.temporal and table.sample and table.hints == ["NOLOCK"]

    top = _statement("SELECT TOP (5) WITH TIES id FROM t")
    assert isinstance(top, ast.Query)
    assert isinstance(top.body, ast.Select)
    assert top.body.top and top.body.top.with_ties


def test_dml_forms_parse_to_typed_nodes() -> None:
    assert isinstance(_statement("INSERT INTO t (id) VALUES (1), (2)"), ast.Insert)
    assert isinstance(_statement("UPDATE t SET id = 2 WHERE id = 1"), ast.Update)
    assert isinstance(_statement("DELETE FROM t WHERE id = 1"), ast.Delete)
    assert isinstance(_statement("TRUNCATE TABLE t RESTART IDENTITY"), ast.Truncate)
    merge = _statement(
        "MERGE INTO t USING s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET id = s.id "
        "WHEN NOT MATCHED BY TARGET THEN INSERT (id) VALUES (s.id)"
    )
    assert isinstance(merge, ast.Merge)
    assert len(merge.clauses) == 2


def test_ddl_security_and_dialect_statements_parse() -> None:
    cases: list[tuple[str, type[ast.Node]]] = [
        ("CREATE TABLE t (id INT PRIMARY KEY, p DECIMAL(10,2))", ast.CreateTable),
        ("CREATE EXTERNAL TABLE t (id INT)", ast.CreateTable),
        ("CREATE MATERIALIZED VIEW v AS SELECT id FROM t", ast.CreateView),
        ("CREATE SECURE VIEW v AS SELECT id FROM t", ast.CreateView),
        ("CREATE UNIQUE INDEX ix ON t (id)", ast.CreateIndex),
        ("CREATE SEQUENCE seq START WITH 1", ast.CreateSequence),
        ("CREATE SYNONYM s FOR t", ast.CreateSynonym),
        ("CREATE TYPE mood AS ENUM ('low', 'high')", ast.CreateRoutine),
        ("CREATE MACRO m AS (SELECT id FROM t)", ast.CreateRoutine),
        ("CREATE SCHEMA sales", ast.CreateSchema),
        ("CREATE DATABASE d", ast.CreateDatabase),
        ("CREATE ROLE analyst", ast.CreatePrincipal),
        (
            "CREATE TRIGGER trg AFTER INSERT ON t FOR EACH ROW BEGIN SET x = 1; END",
            ast.CreateRoutine,
        ),
        ("CREATE PROCEDURE p() BEGIN SELECT 1; END", ast.CreateRoutine),
        ("CREATE FUNCTION f(x INT) RETURNS INT RETURN x + 1", ast.CreateRoutine),
        ("ALTER TABLE t ADD COLUMN x INT", ast.Alter),
        ("ALTER TABLE t DROP COLUMN x", ast.Alter),
        ("ALTER TABLE t RENAME COLUMN old_x TO new_x", ast.Alter),
        ("DROP TABLE IF EXISTS t CASCADE", ast.Drop),
        ("COMMENT ON TABLE t IS 'table'", ast.CommentOn),
        ("RENAME TABLE old_t TO new_t", ast.Rename),
        ("GRANT SELECT ON TABLE t TO analyst WITH GRANT OPTION", ast.Grant),
        ("REVOKE SELECT ON TABLE t FROM analyst", ast.Revoke),
        ("COPY t FROM 'input.csv'", ast.DialectStatement),
        ("LOAD DATA INPATH 'input' INTO TABLE t", ast.DialectStatement),
    ]
    for sql, expected_type in cases:
        node = _statement(sql)
        assert isinstance(node, expected_type), (sql, type(node), Parser().parse(sql).issues)
        assert node.span.end_line >= node.span.start_line


def test_dynamic_sql_and_procedural_case() -> None:
    dynamic = _statement("EXECUTE IMMEDIATE 'SELECT id FROM source_table'")
    assert isinstance(dynamic, ast.DynamicSql)
    assert dynamic.parsed
    assert isinstance(dynamic.parsed[0], ast.Query)

    prepared = _statement("PREPARE stmt FROM 'SELECT id FROM source_table'")
    assert isinstance(prepared, ast.DynamicSql)
    assert prepared.parsed

    case = _statement("CASE WHEN x > 0 THEN 1 ELSE 0 END")
    assert isinstance(case, ast.Case)


def test_psm_blocks_and_cursor_statements() -> None:
    node = _statement(
        "BEGIN "
        "DECLARE x INT DEFAULT 1; "
        "DECLARE c CURSOR FOR SELECT id FROM t; "
        "IF x > 0 THEN SET x = x + 1; ELSE SET x = 0; END IF; "
        "WHILE x < 3 DO SET x = x + 1; END WHILE; "
        "OPEN c; FETCH NEXT FROM c INTO x; CLOSE c; "
        "END"
    )
    assert isinstance(node, ast.Block)
    assert len(node.declarations) == 2
    assert any(isinstance(item, ast.If) for item in node.statements)
    assert any(isinstance(item, ast.While) for item in node.statements)
    assert any(isinstance(item, ast.OpenCursor) for item in node.statements)
    assert any(isinstance(item, ast.Fetch) for item in node.statements)
    assert any(isinstance(item, ast.CloseCursor) for item in node.statements)
    for sql, expected in (
        ("LOOP EXIT; END LOOP", ast.Loop),
        ("FOR i IN 1..3 DO SET x = i; END FOR", ast.ForLoop),
        ("REPEAT SET x = x + 1; UNTIL x > 2 END REPEAT", ast.Repeat),
    ):
        assert isinstance(_statement(sql), expected)


def test_recovery_and_limits_emit_unparsed_nodes_and_issues() -> None:
    recovered = Parser().parse("SELECT FROM; SELECT 2;")
    assert any(isinstance(item, ast.Unparsed) for item in recovered.statements)
    assert len(recovered.statements) >= 2
    assert recovered.issues

    limited = ParserBase()
    limited.expression_depth_limit = 12
    result = limited.parse("SELECT " + "(" * 20 + "1" + ")" * 20)
    assert any(issue.code == "parser.expression_depth_limit" for issue in result.issues)
    assert any(isinstance(item, ast.Unparsed) for item in result.statements)

    clock_values = iter((100.0, 130.1))
    timed = ParserBase(clock=lambda: next(clock_values))
    timed.time_check_interval = 1
    result = timed.parse("SELECT " + " + ".join(["1"] * 20))
    assert any(issue.code == "parser.time_limit" for issue in result.issues)


def test_expression_operator_table_and_public_extension_hooks() -> None:
    assert ParserBase.binary_ops["*"] == (60, 61, "*")
    assert ParserBase.binary_ops["^"] == (70, 70, "^")
    assert ParserBase().parse_primary_ext() is None
    assert ParserBase().parse_postfix_ext(ast.Literal()) is None
    assert ParserBase().parse_statement_ext() is None
