"""Control-M expressions (§13): %%VAR params, scheduling/filter tokens.
Thin subclass of EtlExprParser for precedence tests."""

from __future__ import annotations

from typing import ClassVar

from migraai.discovery.lexer import LexerConfig
from migraai.etl.python.expr import EtlExprParser, parse_expression


class ControlMExprParser(EtlExprParser):
    tool: ClassVar[str] = "controlm"
    lexer_config: ClassVar[LexerConfig] = LexerConfig(
        param_styles=frozenset({"question", "colon", "dollar_numeric", "at", "ampersand"}),
    )


def parse(text: str, line: int = 1):
    return parse_expression(ControlMExprParser, text, line)
