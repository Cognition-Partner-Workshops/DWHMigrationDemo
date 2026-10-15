"""Expression-parsing helper for ETL expression languages.

Each ETL expression language subclasses ``EtlExprParser`` (a ParserBase with an
overridable token rewrite hook), sets a ``lexer_config``, and optionally
extends ``binary_ops``. ``parse_expression`` lexes the text with the core
Lexer, applies the language's token rewrites, and parses a single expression
that must consume the whole input.
"""

from __future__ import annotations

from typing import ClassVar

from migraai.discovery import ast
from migraai.discovery.lexer import Lexer, LexerConfig, Token, TokenKind
from migraai.discovery.parser_base import ParseError, ParseLimitError, ParserBase
from migraai.etl.python.common import redact, span


class EtlExprParser(ParserBase):
    lexer_config: ClassVar[LexerConfig] = LexerConfig()
    tool: ClassVar[str] = "etl"

    def rewrite_tokens(self, tokens: list[Token]) -> list[Token]:
        return tokens


def parse_expression(
    parser_cls: type[EtlExprParser],
    text: str,
    line: int = 1,
) -> tuple[ast.Node, list[ast.Issue]]:
    issues: list[ast.Issue] = []
    try:
        lexer = Lexer(text, parser_cls.lexer_config)
        tokens = lexer.tokenize()
        for issue in lexer.issues:
            issues.append(issue)
        parser = parser_cls(tokens=[])
        tokens = parser.rewrite_tokens(tokens)
        parser.tokens = tokens
        node = parser.parse_expr()
        if parser.peek().kind is not TokenKind.EOF:
            raise ParseError("Unexpected trailing tokens in expression.", parser.peek())
        _shift_span(node, line - 1)
    except ParseLimitError as error:
        issues.append(ast.Issue("error", error.code, str(error), span(line + error.token.line - 1)))
        node = ast.Unparsed(text=redact(text), reason=str(error), span=span(line))
    except (ParseError, RecursionError) as error:
        message = str(error)
        token = getattr(error, "token", None)
        issues.append(
            ast.Issue(
                "error",
                f"{parser_cls.tool}.expression",
                message or "Expression parse failed.",
                span(line + token.line - 1) if token is not None else span(line),
            )
        )
        node = ast.Unparsed(text=redact(text), reason=message or "expression", span=span(line))
    return node, issues


def _shift_span(node: ast.Node, offset: int) -> None:
    if offset == 0:
        return
    for item in node.walk():
        item.span = ast.Span(
            item.span.start_line + offset,
            item.span.start_col,
            item.span.end_line + offset,
            item.span.end_col,
        )


__all__ = ["EtlExprParser", "parse_expression"]
