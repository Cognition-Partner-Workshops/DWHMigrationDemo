"""DataStage expression language (§13): lnk.col, `:` concat, If…Then…Else,
= <> <, And/Or/Not, functions, #param#, strings."""

from __future__ import annotations

import re
from typing import ClassVar

from migraai.discovery.lexer import LexerConfig, Token, TokenKind
from migraai.etl.python.expr import EtlExprParser, parse_expression

_PARAM_RE = re.compile(r"#[A-Za-z_][A-Za-z_0-9.]*#")


class DataStageExprParser(EtlExprParser):
    tool: ClassVar[str] = "datastage"
    lexer_config: ClassVar[LexerConfig] = LexerConfig(
        ident_quotes={'"': '"'},
        extra_operators=(":",),
    )

    def rewrite_tokens(self, tokens: list[Token]) -> list[Token]:
        out: list[Token] = []
        for token in tokens:
            if token.kind is TokenKind.OPERATOR and token.text == ":":
                out.append(
                    Token(TokenKind.OPERATOR, "||", "||", token.line, token.col, token.offset)
                )
            else:
                out.append(token)
        return out


def parse(text: str, line: int = 1):
    """Parse a DataStage expression; returns (node, issues)."""
    rewritten = _PARAM_RE.sub(lambda m: f":{m.group(0)[1:-1].replace('.', '_')}", text)
    return parse_expression(DataStageExprParser, rewritten, line)
