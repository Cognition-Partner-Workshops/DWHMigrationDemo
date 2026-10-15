"""SSIS expression language (§13): @[User::X] variables, == != && || !, ? :,
casts (DT_*, n) expr, functions, strings with \\ escapes."""

from __future__ import annotations

from typing import ClassVar

from migraai.discovery.lexer import LexerConfig, Token, TokenKind
from migraai.etl.python.expr import EtlExprParser, parse_expression


class SsisExprParser(EtlExprParser):
    tool: ClassVar[str] = "ssis"
    lexer_config: ClassVar[LexerConfig] = LexerConfig(
        param_styles=frozenset({"at"}),
    )

    def rewrite_tokens(self, tokens: list[Token]) -> list[Token]:
        out: list[Token] = []
        for token in tokens:
            if token.kind is TokenKind.OPERATOR and token.text == "==":
                token = Token(TokenKind.OPERATOR, "=", "=", token.line, token.col, token.offset)
            elif token.kind is TokenKind.OPERATOR and token.text == "&&":
                token = Token(TokenKind.IDENT, "AND", "and", token.line, token.col, token.offset)
            elif token.kind is TokenKind.OPERATOR and token.text == "||":
                token = Token(TokenKind.IDENT, "OR", "or", token.line, token.col, token.offset)
            out.append(token)
        return out


def parse(text: str, line: int = 1):
    """Parse an SSIS expression; returns (node, issues)."""
    return parse_expression(SsisExprParser, text, line)
