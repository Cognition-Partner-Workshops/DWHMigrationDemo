from __future__ import annotations

import pytest

import migraai.discovery.parser_base as parser_base
from migraai.discovery import ast
from migraai.discovery.parser_base import ParserBase
from migraai.discovery.parsers.ansi import Parser


def test_broken_statement_in_middle_does_not_block_later_statements() -> None:
    result = Parser().parse("SELECT 1; SELECT FROM; SELECT 3", "recovery.sql")
    assert [statement.kind for statement in result.statements] == ["query", "unparsed", "query"]
    assert result.issues


def test_unterminated_string_and_comment_are_reported_without_raising() -> None:
    for sql in ("SELECT 'unfinished", "SELECT 1 /* unfinished"):
        result = Parser().parse(sql, "unterminated.sql")
        assert result.statements
        assert any(issue.code.startswith("lexer.") for issue in result.issues)


def test_stray_end_recovers_to_next_statement() -> None:
    result = Parser().parse("END; SELECT 7", "stray-end.sql")
    assert [statement.kind for statement in result.statements] == ["unparsed", "query"]


def test_procedural_block_preserves_unparsed_child_and_later_statement() -> None:
    result = Parser().parse(
        "CREATE PROCEDURE p() BEGIN SET x = 1; nonsense; SET y = 2; END",
        "partial.sql",
    )
    routine = result.statements[0]
    assert isinstance(routine, ast.CreateRoutine)
    assert isinstance(routine.body, ast.Block)
    assert [statement.kind for statement in routine.body.statements] == [
        "set_statement",
        "unparsed",
        "set_statement",
    ]
    assert result.issues


def test_default_depth_limit_is_500_and_rejects_deep_parentheses() -> None:
    parser = Parser()
    result = parser.parse(f"SELECT {'(' * 510}1{')' * 510}", "deep.sql")
    assert parser.expression_depth_limit == 500
    assert any(issue.code == "parser.expression_depth_limit" for issue in result.issues)
    assert any(isinstance(statement, ast.Unparsed) for statement in result.statements)


def test_file_time_budget_uses_injected_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter((100.0, 131.0))
    monkeypatch.setattr(parser_base.time, "monotonic", lambda: next(times))
    parser = ParserBase()
    parser.time_check_interval = 1
    result = parser.parse("SELECT 1 + 2", "budget.sql")
    assert parser.file_time_limit_seconds == 30.0
    assert any(issue.code == "parser.time_limit" for issue in result.issues)
