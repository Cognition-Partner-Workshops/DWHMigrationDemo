from __future__ import annotations

import re

from migraai.discovery.lexer import Lexer, LexerConfig, TokenKind


class TSqlLexer(Lexer):
    _pseudo_column = re.compile(r"\$action(?![\w@$#])", re.IGNORECASE)

    def _scan_operator(self) -> bool:
        match = self._pseudo_column.match(self.text, self._offset)
        if match:
            raw = match.group(0)
            self._emit(TokenKind.IDENT, raw, raw)
            self._advance(raw)
            return True
        return super()._scan_operator()


LEXER_CONFIG = LexerConfig(
    ident_quotes={"[": "]", '"': '"'},
    batch_separators=(r"^\s*GO(?:\s+\d+)?\s*$",),
    param_styles=frozenset({"at"}),
    hash_identifiers=True,
)


__all__ = ["LEXER_CONFIG", "TSqlLexer"]
