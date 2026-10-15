from __future__ import annotations

from migraai.discovery import ast
from migraai.discovery.lexer import LexerConfig
from migraai.etl.python.expr import EtlExprParser, parse_expression


class DemoExpr(EtlExprParser):
    tool = "python"
    lexer_config = LexerConfig()


def test_parse_expression_precedence() -> None:
    node, issues = parse_expression(DemoExpr, "a + b * 2")
    assert issues == []
    assert isinstance(node, ast.Binary) and node.op == "+"
    assert isinstance(node.right, ast.Binary) and node.right.op == "*"


def test_parse_expression_columnref_and_call() -> None:
    node, _ = parse_expression(DemoExpr, "f(t.col, 1)")
    assert isinstance(node, ast.FunctionCall)
    assert node.name.parts[0].value == "f"
    assert isinstance(node.args[0], ast.ColumnRef)


def test_parse_expression_error_recovery() -> None:
    node, issues = parse_expression(DemoExpr, "a + * b")
    assert isinstance(node, ast.Unparsed)
    assert issues and issues[0].severity == "error"


def test_parse_expression_trailing_tokens() -> None:
    node, issues = parse_expression(DemoExpr, "1 2")
    assert isinstance(node, ast.Unparsed) and issues


def test_parse_expression_line_shift() -> None:
    node, _ = parse_expression(DemoExpr, "x + 1", line=10)
    assert node.span.start_line == 10
