"""Informatica expression language (§13): IIF/DECODE, || concat, AND/OR/NOT,
`=`,`!=`,`<>`,`^=`, `+ - * / %`, `$$PARAM`/`$Param`/`$PMxxx` params, `:LKP.x`."""

from __future__ import annotations

import re
from typing import ClassVar

from migraai.discovery.lexer import LexerConfig, Token
from migraai.etl.python.expr import EtlExprParser, parse_expression

_LKP_RE = re.compile(r":LKP\.([A-Za-z_][A-Za-z_0-9]*)", re.IGNORECASE)
_PARAM_RE = re.compile(
    r"\$\$[A-Za-z_][A-Za-z_0-9]*|\$[A-Za-z_][A-Za-z_0-9]*|\$\{[A-Za-z_][A-Za-z_0-9]*\}"
)


class InformaticaExprParser(EtlExprParser):
    tool: ClassVar[str] = "informatica"
    lexer_config: ClassVar[LexerConfig] = LexerConfig(
        param_styles=frozenset({"question", "colon", "dollar_numeric"}),
    )

    def rewrite_tokens(self, tokens: list[Token]) -> list[Token]:
        return tokens


def parse(text: str, line: int = 1):
    """Parse an Informatica expression; returns (node, issues, lookups)."""
    lookups = sorted(set(m.group(1) for m in _LKP_RE.finditer(text)))
    rewritten = _LKP_RE.sub(lambda m: f"LKP_{m.group(1)}", text)
    node, issues = parse_expression(InformaticaExprParser, rewritten, line)
    return node, issues, lookups
