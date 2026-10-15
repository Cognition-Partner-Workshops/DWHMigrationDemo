"""DataStage expression language (§13): lnk.col, `:` concat, If…Then…Else,
= <> <, And/Or/Not, functions, #param# / #ps.param# → Param, strings."""

from __future__ import annotations

import re
from typing import ClassVar

from migraai.discovery.lexer import LexerConfig, Token, TokenKind
from migraai.etl.python.expr import EtlExprParser, parse_expression

_PARAM_RE = re.compile(r"#([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?)#")
_IF_THEN_ELSE_RE = re.compile(
    r"\bIf\s+(.+?)\s+Then\s+(.+?)\s+Else\s+(.+)\s*$", re.IGNORECASE | re.DOTALL
)


class DataStageExprParser(EtlExprParser):
    tool: ClassVar[str] = "datastage"
    lexer_config: ClassVar[LexerConfig] = LexerConfig(
        ident_quotes={'"': '"'},
        param_styles=frozenset({"question", "at"}),
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
        # merge PARAM + '.' + IDENT chains → one PARAM (`@ps.param`)
        merged: list[Token] = []
        i = 0
        while i < len(out):
            tok = out[i]
            if (
                tok.kind is TokenKind.PARAM
                and i + 2 < len(out)
                and out[i + 1].kind is TokenKind.PUNCT
                and out[i + 1].text == "."
                and out[i + 2].kind is TokenKind.IDENT
            ):
                text = tok.text + "." + out[i + 2].text
                merged.append(Token(TokenKind.PARAM, text, text, tok.line, tok.col, tok.offset))
                i += 3
            else:
                merged.append(tok)
                i += 1
        return merged


def parse(text: str, line: int = 1):
    """Parse a DataStage expression; returns (node, issues)."""
    rewritten = _PARAM_RE.sub(lambda m: f"@{m.group(1)}", text)
    match = _IF_THEN_ELSE_RE.match(rewritten.strip())
    if match:
        cond, then, else_ = match.groups()
        rewritten = f"CASE WHEN {cond} THEN {then} ELSE {else_} END"
    return parse_expression(DataStageExprParser, rewritten, line)
