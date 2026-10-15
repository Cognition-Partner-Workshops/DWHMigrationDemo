"""Informatica expression language (§13): IIF/DECODE, || concat, AND/OR/NOT,
`=`,`!=`,`<>`,`^=`, `+ - * / %`, `$$PARAM`/`$Param`/`$PMxxx` params,
`:LKP.x(args)` lookup calls, `:SP.x(...)` stored-procedure calls."""

from __future__ import annotations

import re
from typing import ClassVar

from migraai.discovery import ast
from migraai.discovery.lexer import LexerConfig, Token
from migraai.etl.python.expr import EtlExprParser, parse_expression

_CALL_RE = re.compile(r":(LKP|SP)\.([A-Za-z_][A-Za-z_0-9]*)\s*\(([^()]*)\)", re.IGNORECASE)
_PARAM_RE = re.compile(
    r"\$\$([A-Za-z_][A-Za-z_0-9]*)|\$([A-Za-z_][A-Za-z_0-9]*)|\$\{([A-Za-z_][A-Za-z_0-9]*)\}"
)


class InformaticaExprParser(EtlExprParser):
    tool: ClassVar[str] = "informatica"
    lexer_config: ClassVar[LexerConfig] = LexerConfig(
        param_styles=frozenset({"question", "colon", "dollar_numeric"}),
    )

    def rewrite_tokens(self, tokens: list[Token]) -> list[Token]:
        return tokens


def parse(text: str, line: int = 1):
    """Parse an Informatica expression.

    Returns (node, issues, calls) where calls are
    ``DialectExpr("informatica.lookup_call" | "informatica.sp_call")`` nodes
    with a ``lookup``/``procedure`` QualifiedName and parsed ``args``.
    """
    calls: list[ast.Node] = []
    rewritten = text

    def _replace(match: re.Match[str]) -> str:
        kind, name, arg_text = match.group(1).upper(), match.group(2), match.group(3)
        arg_nodes = []
        if arg_text.strip():
            for piece in arg_text.split(","):
                node, _issues = parse_expression(InformaticaExprParser, piece.strip(), line)
                arg_nodes.append(node)
        node = ast.DialectExpr(
            name=f"informatica.{'lookup_call' if kind == 'LKP' else 'sp_call'}",
            args={
                "lookup" if kind == "LKP" else "procedure": ast.QualifiedName(
                    parts=[ast.Identifier(value=name)]
                ),
                "args": arg_nodes,
            },
        )
        calls.append(node)
        return f"{kind}_{name}({arg_text})" if arg_text.strip() else f"{kind}_{name}()"

    rewritten = _CALL_RE.sub(_replace, rewritten)
    rewritten = _PARAM_RE.sub(lambda m: ":" + next(g for g in m.groups() if g), rewritten)
    node, issues = parse_expression(InformaticaExprParser, rewritten, line)
    return node, issues, calls
