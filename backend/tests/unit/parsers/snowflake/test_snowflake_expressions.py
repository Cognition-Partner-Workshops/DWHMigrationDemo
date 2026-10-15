from migraai.discovery import ast
from migraai.discovery.parsers.snowflake import Parser


def _projection(sql: str) -> ast.Node:
    result = Parser().parse(sql, "expr.sql")
    assert not result.issues, result.issues
    assert isinstance(result.statements[0], ast.Query)
    assert isinstance(result.statements[0].body, ast.Select)
    return result.statements[0].body.projections[0].expr


def test_snowflake_json_path_cast_and_access_chain() -> None:
    expr = _projection("SELECT col:a.b[0]::STRING")
    assert isinstance(expr, ast.Cast) and expr.style == "double_colon"
    assert isinstance(expr.expr, ast.Subscript)
    assert isinstance(expr.expr.expr, ast.FieldAccess)
    assert isinstance(expr.expr.expr.expr, ast.DialectExpr)
    assert expr.expr.expr.expr.name == "json_path"


def test_snowflake_expression_precedence_and_bind_variables() -> None:
    expr = _projection("SELECT a::INT + 1")
    assert isinstance(expr, ast.Binary) and expr.op == "+"
    assert isinstance(expr.left, ast.Cast)
    assert isinstance(_projection("SELECT -x::INT"), ast.Unary)
    assert isinstance(_projection("SELECT :value"), ast.Variable)
    assert isinstance(_projection("SELECT $v"), ast.Param)
    comparison = _projection("SELECT x:a::INT = 1 AND y ILIKE ANY ('a%', 'b%')")
    assert isinstance(comparison, ast.Binary) and comparison.op == "and"
    assert isinstance(comparison.right, ast.Like)
    assert comparison.right.op == "ilike" and comparison.right.quantifier == "any"


def test_snowflake_identifier_function_resolves_literal_and_marks_dynamic() -> None:
    result = Parser().parse("SELECT * FROM IDENTIFIER('db.sch.orders')", "identifier.sql")
    assert not result.issues
    table = result.statements[0].body.from_[0]
    assert isinstance(table, ast.TableRef)
    assert [part.value for part in table.name.parts] == ["db", "sch", "orders"]
    dynamic = Parser().parse("SELECT * FROM IDENTIFIER(:table_name)", "dynamic.sql")
    assert dynamic.statements
    assert any(issue.code == "snowflake.dynamic_identifier" for issue in dynamic.issues)


def test_snowflake_lateral_flatten_and_table_function() -> None:
    node = Parser().parse(
        "SELECT f.value FROM src, LATERAL FLATTEN(input => src.payload, path => 'a') f",
        "flatten.sql",
    ).statements[0]
    assert isinstance(node, ast.Query)
    assert isinstance(node.body.from_[1], ast.TableFunctionRef)
    assert node.body.from_[1].lateral
    table = Parser().parse("SELECT * FROM TABLE(FLATTEN(input => t.v)) f", "table.sql")
    assert isinstance(table.statements[0].body.from_[0], ast.TableFunctionRef)


def test_snowflake_temporal_table_refs_and_stage_tables() -> None:
    result = Parser().parse(
        "SELECT $1 FROM db.s.t AT(TIMESTAMP => '2025-01-01') a "
        "JOIN @landing/path/file.csv s ON a.id = s.id",
        "temporal.sql",
    )
    assert not result.issues, result.issues
    left, right = result.statements[0].body.from_[0].left, result.statements[0].body.from_[0].right
    assert isinstance(left, ast.TableRef) and left.temporal.name == "temporal"
    assert isinstance(right, ast.TableRef) and right.hints == ["stage"]
