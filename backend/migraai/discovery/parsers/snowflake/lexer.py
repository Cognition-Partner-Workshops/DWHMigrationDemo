from __future__ import annotations

import re

from migraai.discovery.lexer import Lexer, LexerConfig, TokenKind


class SnowflakeLexer(Lexer):
    def __init__(self, text: str) -> None:
        super().__init__(
            text,
            LexerConfig(
                ident_quotes={'"': '"'},
                string_escapes="backslash",
                dollar_quoting=True,
                param_styles=frozenset(),
                extra_operators=("=>", "::"),
            ),
        )

    def _scan_parameter(self) -> bool:
        remaining = self.text[self._offset :]
        stage = re.match(
            r"@(?:%[A-Za-z_][\w$]*|~|[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*(?:/[^\s,;()]+)?)",
            remaining,
        )
        parameter = stage or re.match(r"\$(?:\d+|[A-Za-z_][\w$]*)", remaining)
        if parameter is None:
            return False
        raw = parameter.group(0)
        self._emit(TokenKind.PARAM, raw, raw)
        self._advance(raw)
        return True

    def _scan_identifier(self) -> bool:
        match = re.match(r"[^\W\d][\w$]*|_[\w$]*", self.text[self._offset :], re.UNICODE)
        if match is None:
            return False
        raw = match.group(0)
        self._emit(TokenKind.IDENT, raw, raw)
        self._advance(raw)
        return True
