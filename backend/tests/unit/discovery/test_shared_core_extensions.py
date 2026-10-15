from __future__ import annotations

from migraai.discovery import ast
from migraai.discovery.lexer import Lexer, LexerConfig, Token, TokenKind
from migraai.discovery.parser_base import ParserBase


def test_parser_uses_injected_lexer_class() -> None:
    instances: list[tuple[str, LexerConfig]] = []

    class RecordingLexer(Lexer):
        def __init__(self, text: str, config: LexerConfig) -> None:
            instances.append((text, config))
            super().__init__(text, config)

    class InjectedParser(ParserBase):
        lexer_class = RecordingLexer

    result = InjectedParser().parse("SELECT 1")

    assert isinstance(result.statements[0], ast.Query)
    assert len(instances) == 1
    assert instances[0][0] == "SELECT 1"


def test_recovery_uses_statement_end_extension_hook() -> None:
    class HookParser(ParserBase):
        seen: list[Token]

        def __init__(self) -> None:
            super().__init__()
            self.seen = []

        def is_statement_end(self, token: Token) -> bool:
            self.seen.append(token)
            return super().is_statement_end(token)

    parser = HookParser()
    result = parser.parse("BROKEN; SELECT 1")

    assert any(token.kind is TokenKind.TERMINATOR for token in parser.seen)
    assert isinstance(result.statements[-1], ast.Query)


def test_hash_identifiers_and_hash_comments_are_distinguished() -> None:
    lexer = Lexer(
        "#t ##t # comment\n#>>",
        LexerConfig(hash_identifiers=True),
    )
    tokens = lexer.tokenize()

    assert [(token.kind, token.text) for token in tokens[:-1]] == [
        (TokenKind.IDENT, "#t"),
        (TokenKind.IDENT, "##t"),
        (TokenKind.OPERATOR, "#>>"),
    ]
    assert [comment.text for comment in lexer.comments] == ["# comment"]


def test_dynamic_terminator_directive_is_kept_and_replaces_active_terminator() -> None:
    lexer = Lexer(
        "--#SET TERMINATOR @\nSELECT 1; SELECT 2@",
        LexerConfig(dynamic_terminators=True),
    )
    tokens = lexer.tokenize()

    assert [token.text for token in tokens if token.kind is TokenKind.TERMINATOR] == ["@"]
    assert any(token.kind is TokenKind.PUNCT and token.text == ";" for token in tokens)
    assert [comment.text for comment in lexer.comments] == ["--#SET TERMINATOR @"]
    assert lexer.issues == []


def test_colon_numeric_parameter_is_one_token() -> None:
    tokens = Lexer(":1 :name", LexerConfig(param_styles=frozenset({"colon"}))).tokenize()

    assert [(token.kind, token.text) for token in tokens[:-1]] == [
        (TokenKind.PARAM, ":1"),
        (TokenKind.PARAM, ":name"),
    ]


def test_minus_is_a_shared_set_operation() -> None:
    result = ParserBase().parse("SELECT 1 MINUS SELECT 2")

    assert result.issues == []
    query = result.statements[0]
    assert isinstance(query, ast.Query)
    assert isinstance(query.body, ast.SetOperation)
    assert query.body.op == "minus"
