from __future__ import annotations

from migraai.discovery.ast import Issue, Span
from migraai.discovery.lexer import Lexer, LexerConfig, TokenKind


class HiveLexer(Lexer):
    def __init__(self, text: str) -> None:
        super().__init__(
            text,
            LexerConfig(
                ident_quotes={"`": "`"},
                string_escapes="backslash",
                param_styles=frozenset({"hivevar"}),
                extra_operators=("&", "|", "^"),
            ),
        )

    def _scan_string(self) -> bool:
        if self.text[self._offset] != '"':
            return super()._scan_string()
        start_line, start_col = self._position()
        start = self._offset
        self._advance('"')
        value: list[str] = []
        terminated = False
        while self._offset < len(self.text):
            char = self.text[self._offset]
            if char == '"':
                if self.text.startswith('""', self._offset):
                    value.append('"')
                    self._advance('""')
                else:
                    self._advance('"')
                    terminated = True
                    break
            elif char == "\\" and self._offset + 1 < len(self.text):
                value.append(self.text[self._offset + 1])
                self._advance(self.text[self._offset : self._offset + 2])
            else:
                value.append(char)
                self._advance(char)
        raw = self.text[start : self._offset]
        self._emit(TokenKind.STRING, raw, "".join(value))
        if not terminated:
            self.issues.append(
                Issue(
                    "error",
                    "lexer.unterminated_string",
                    "Unterminated string literal.",
                    Span(
                        start_line,
                        start_col,
                        self._line,
                        self._col,
                    ),
                )
            )
        return True

    def _scan_operator(self) -> bool:
        if self.text.startswith(">>", self._offset):
            self._emit(TokenKind.OPERATOR, ">", ">")
            self._advance(">")
            return True
        return super()._scan_operator()
