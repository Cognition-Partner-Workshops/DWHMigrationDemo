from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from migraai.discovery.ast import Issue, Span


class TokenKind(StrEnum):
    IDENT = "IDENT"
    QUOTED_IDENT = "QUOTED_IDENT"
    NUMBER = "NUMBER"
    STRING = "STRING"
    NATIONAL_STRING = "NATIONAL_STRING"
    BIT_STRING = "BIT_STRING"
    HEX_STRING = "HEX_STRING"
    PARAM = "PARAM"
    OPERATOR = "OPERATOR"
    PUNCT = "PUNCT"
    DOLLAR_BODY = "DOLLAR_BODY"
    DOT_COMMAND = "DOT_COMMAND"
    TERMINATOR = "TERMINATOR"
    EOF = "EOF"


@dataclass(slots=True, frozen=True)
class Token:
    kind: TokenKind
    text: str
    value: str
    line: int
    col: int
    offset: int


@dataclass(slots=True, frozen=True)
class Comment:
    text: str
    span: Span


@dataclass(slots=True, frozen=True)
class LexerConfig:
    ident_quotes: Mapping[str, str] = field(default_factory=lambda: {'"': '"'})
    string_escapes: str = "double"
    oracle_q_quotes: bool = False
    nested_comments: bool = False
    dollar_quoting: bool = False
    batch_separators: tuple[str, ...] = ()
    statement_terminators: tuple[str, ...] = (";",)
    dot_command_prefixes: tuple[str, ...] = (".",)
    param_styles: frozenset[str] = frozenset(
        {"question", "colon", "dollar_numeric", "at", "ampersand", "hivevar"}
    )
    extra_operators: tuple[str, ...] = ()


_OPERATORS = (
    "#>>",
    "!~*",
    "<=>",
    "->>",
    "..",
    "!~",
    "!<",
    "!>",
    "!=",
    "!~",
    "::",
    ":=",
    "||",
    "&&",
    "<=",
    ">=",
    "<>",
    "==",
    "=>",
    "**",
    "^=",
    "->",
    "#>",
    "??",
    "?|",
    "?&",
)
_NUMBER_RE = re.compile(
    r"(?:0[xX][0-9A-Fa-f]+|0[bB][01]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)
_IDENT_RE = re.compile(r"[^\W\d]\w*|\w+", re.UNICODE)


class Lexer:
    def __init__(self, text: str, config: LexerConfig | None = None) -> None:
        self.text = text
        self.config = config or LexerConfig()
        self.tokens: list[Token] = []
        self.comments: list[Comment] = []
        self.issues: list[Issue] = []
        self._offset = 0
        self._line = 1
        self._col = 1
        self._line_start = 0

    def tokenize(self) -> list[Token]:
        if self.tokens:
            return self.tokens
        while self._offset < len(self.text):
            try:
                if self._scan_line_control():
                    continue
                char = self.text[self._offset]
                if char.isspace():
                    self._advance(char)
                elif self.text.startswith("--", self._offset) or (
                    char == "#"
                    and not any(
                        self.text.startswith(operator, self._offset)
                        for operator in (*_OPERATORS, *self.config.extra_operators)
                    )
                ):
                    self._scan_line_comment()
                elif self.text.startswith("/*", self._offset):
                    self._scan_block_comment()
                elif self._scan_special_string():
                    continue
                elif self._scan_quoted_identifier():
                    continue
                elif self._scan_string():
                    continue
                elif self._scan_parameter():
                    continue
                elif self._scan_number():
                    continue
                elif self._scan_identifier():
                    continue
                elif self._scan_operator():
                    continue
                else:
                    self._emit_unknown()
            except Exception as error:
                start = self._position()
                char = self.text[self._offset] if self._offset < len(self.text) else ""
                self.issues.append(
                    Issue(
                        "error",
                        "lexer.internal_error",
                        f"Lexer recovered from {type(error).__name__}.",
                        Span(*start, *start),
                    )
                )
                if char:
                    self._emit(TokenKind.OPERATOR, char, char)
                    self._advance(char)
        self.tokens.append(Token(TokenKind.EOF, "", "", self._line, self._col, self._offset))
        return self.tokens

    def __iter__(self):
        if not self.tokens:
            self.tokenize()
        return iter(self.tokens)

    def _position(self) -> tuple[int, int]:
        return self._line, self._col

    def _advance(self, text: str) -> None:
        newlines = text.count("\n")
        if newlines:
            self._line += newlines
            self._col = len(text.rsplit("\n", 1)[-1]) + 1
            self._line_start = self._offset + len(text.rsplit("\n", 1)[0]) + 1
        else:
            self._col += len(text)
        self._offset += len(text)

    def _span(self, line: int, col: int, text: str) -> Span:
        newlines = text.count("\n")
        if newlines:
            return Span(line, col, line + newlines, len(text.rsplit("\n", 1)[-1]) + 1)
        return Span(line, col, line, col + len(text))

    def _emit(self, kind: TokenKind, text: str, value: str) -> None:
        self.tokens.append(Token(kind, text, value, self._line, self._col, self._offset))

    def _scan_line_control(self) -> bool:
        if self._offset != self._line_start:
            return False
        end = self.text.find("\n", self._offset)
        if end < 0:
            end = len(self.text)
            newline = ""
        else:
            newline = "\n"
        line_text = self.text[self._offset : end].rstrip("\r")
        for pattern in self.config.batch_separators:
            try:
                matched = re.fullmatch(pattern, line_text, flags=re.IGNORECASE)
            except re.error:
                self.issues.append(
                    Issue("warning", "lexer.invalid_batch_pattern", "Invalid batch pattern.")
                )
                continue
            if matched:
                raw = self.text[self._offset : end]
                self._emit(TokenKind.TERMINATOR, raw, line_text.strip())
                self._advance(raw + newline)
                return True
        stripped = line_text.lstrip()
        if any(stripped.startswith(prefix) for prefix in self.config.dot_command_prefixes):
            raw = self.text[self._offset : end]
            self._emit(TokenKind.DOT_COMMAND, raw, line_text.strip())
            self._advance(raw + newline)
            return True
        return False

    def _scan_line_comment(self) -> None:
        line, col = self._position()
        end = self.text.find("\n", self._offset)
        if end < 0:
            end = len(self.text)
        raw = self.text[self._offset : end]
        self.comments.append(Comment(raw, self._span(line, col, raw)))
        self._advance(raw)

    def _scan_block_comment(self) -> None:
        line, col = self._position()
        start = self._offset
        self._advance("/*")
        depth = 1
        while self._offset < len(self.text) and depth:
            if self.text.startswith("/*", self._offset) and self.config.nested_comments:
                depth += 1
                self._advance("/*")
            elif self.text.startswith("*/", self._offset):
                depth -= 1
                self._advance("*/")
            else:
                self._advance(self.text[self._offset])
        raw = self.text[start : self._offset]
        self.comments.append(Comment(raw, self._span(line, col, raw)))
        if depth:
            self.issues.append(
                Issue(
                    "error",
                    "lexer.unterminated_comment",
                    "Unterminated block comment.",
                    self._span(line, col, raw),
                )
            )

    def _scan_special_string(self) -> bool:
        remaining = self.text[self._offset :]
        if self.config.oracle_q_quotes:
            match = re.match(r"(?i)(?:n)?q'(.)", remaining, flags=re.DOTALL)
            if match and not match.group(1).isalnum() and not match.group(1).isspace():
                prefix = match.group(0)
                opening = match.group(1)
                closing = {"[": "]", "(": ")", "{": "}", "<": ">"}.get(opening, opening)
                start_line, start_col = self._position()
                start = self._offset
                value_start = start + len(prefix)
                end_marker = closing + "'"
                end = self.text.find(end_marker, value_start)
                if end < 0:
                    end = len(self.text)
                    value = self.text[value_start:]
                    raw = self.text[start:end]
                    self._emit(TokenKind.STRING, raw, value)
                    self._advance(raw)
                    self.issues.append(
                        Issue(
                            "error",
                            "lexer.unterminated_q_quote",
                            "Unterminated Oracle q-quoted string.",
                            self._span(start_line, start_col, raw),
                        )
                    )
                else:
                    end += len(end_marker)
                    raw = self.text[start:end]
                    self._emit(TokenKind.STRING, raw, self.text[value_start : end - 2])
                    self._advance(raw)
                return True
        if self.config.dollar_quoting and self.text[self._offset] == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$", remaining)
            if match:
                delimiter = match.group(0)
                start_line, start_col = self._position()
                start = self._offset
                value_start = start + len(delimiter)
                end = self.text.find(delimiter, value_start)
                if end < 0:
                    raw = self.text[start:]
                    self._emit(TokenKind.DOLLAR_BODY, raw, self.text[value_start:])
                    self._advance(raw)
                    self.issues.append(
                        Issue(
                            "error",
                            "lexer.unterminated_dollar_body",
                            "Unterminated dollar-quoted body.",
                            self._span(start_line, start_col, raw),
                        )
                    )
                else:
                    end += len(delimiter)
                    raw = self.text[start:end]
                    self._emit(
                        TokenKind.DOLLAR_BODY, raw, self.text[value_start : end - len(delimiter)]
                    )
                    self._advance(raw)
                return True
        return False

    def _scan_quoted_identifier(self) -> bool:
        char = self.text[self._offset]
        closing = self.config.ident_quotes.get(char)
        if closing is None:
            return False
        start_line, start_col = self._position()
        start = self._offset
        self._advance(char)
        value: list[str] = []
        while self._offset < len(self.text):
            current = self.text[self._offset]
            if current == closing:
                if self.text.startswith(closing + closing, self._offset):
                    value.append(closing)
                    self._advance(closing + closing)
                else:
                    self._advance(closing)
                    raw = self.text[start : self._offset]
                    self._emit(TokenKind.QUOTED_IDENT, raw, "".join(value))
                    return True
            else:
                value.append(current)
                self._advance(current)
        raw = self.text[start : self._offset]
        self._emit(TokenKind.QUOTED_IDENT, raw, "".join(value))
        self.issues.append(
            Issue(
                "error",
                "lexer.unterminated_identifier",
                "Unterminated quoted identifier.",
                self._span(start_line, start_col, raw),
            )
        )
        return True

    def _scan_string(self) -> bool:
        remaining = self.text[self._offset :]
        prefix_kind: TokenKind | None = None
        prefix_len = 0
        for prefix, kind in (
            ("N'", TokenKind.NATIONAL_STRING),
            ("X'", TokenKind.HEX_STRING),
            ("B'", TokenKind.BIT_STRING),
        ):
            if remaining[:2].casefold() == prefix.casefold():
                prefix_kind = kind
                prefix_len = 1
                break
        quote_offset = self._offset + prefix_len
        if self.text[quote_offset] != "'":
            return False
        start_line, start_col = self._position()
        start = self._offset
        if prefix_len:
            self._advance(self.text[self._offset : self._offset + prefix_len])
        self._advance("'")
        value: list[str] = []
        terminated = False
        while self._offset < len(self.text):
            char = self.text[self._offset]
            if char == "'":
                if self.text.startswith("''", self._offset):
                    value.append("'")
                    self._advance("''")
                else:
                    self._advance("'")
                    terminated = True
                    break
            elif char == "\\" and self.config.string_escapes == "backslash":
                if self._offset + 1 < len(self.text):
                    value.append(self.text[self._offset + 1])
                    self._advance(self.text[self._offset : self._offset + 2])
                else:
                    value.append(char)
                    self._advance(char)
            else:
                value.append(char)
                self._advance(char)
        raw = self.text[start : self._offset]
        self._emit(prefix_kind or TokenKind.STRING, raw, "".join(value))
        if not terminated:
            self.issues.append(
                Issue(
                    "error",
                    "lexer.unterminated_string",
                    "Unterminated string literal.",
                    self._span(start_line, start_col, raw),
                )
            )
        return True

    def _scan_parameter(self) -> bool:
        start = self._offset
        remaining = self.text[start:]
        styles = self.config.param_styles
        matched = ""
        if "hivevar" in styles and remaining.startswith("${"):
            end = remaining.find("}")
            if end >= 0:
                matched = remaining[: end + 1]
        elif "question" in styles and remaining.startswith("?"):
            matched = "?"
        elif "colon" in styles and remaining.startswith(":") and not remaining.startswith("::"):
            match = re.match(r":[A-Za-z_][A-Za-z_0-9$]*", remaining)
            if match:
                matched = match.group(0)
        elif "at" in styles and remaining.startswith("@"):
            match = re.match(r"@@?[A-Za-z_][A-Za-z_0-9$]*", remaining)
            if match:
                matched = match.group(0)
        elif "ampersand" in styles and remaining.startswith("&"):
            match = re.match(r"&[A-Za-z_][A-Za-z_0-9$]*", remaining)
            if match:
                matched = match.group(0)
        elif "dollar_numeric" in styles and remaining.startswith("$"):
            match = re.match(r"\$\d+", remaining)
            if match:
                matched = match.group(0)
        if not matched:
            return False
        self._emit(TokenKind.PARAM, matched, matched)
        self._advance(matched)
        return True

    def _scan_number(self) -> bool:
        match = _NUMBER_RE.match(self.text, self._offset)
        if not match:
            return False
        raw = match.group(0)
        if raw.endswith(".") and self.text.startswith("..", self._offset + len(raw) - 1):
            raw = raw[:-1]
        self._emit(TokenKind.NUMBER, raw, raw)
        self._advance(raw)
        return True

    def _scan_identifier(self) -> bool:
        match = _IDENT_RE.match(self.text, self._offset)
        if not match:
            return False
        raw = match.group(0)
        self._emit(TokenKind.IDENT, raw, raw)
        self._advance(raw)
        return True

    def _scan_operator(self) -> bool:
        remaining = self.text[self._offset :]
        if remaining.startswith("(+)"):
            self._emit(TokenKind.OPERATOR, "(+)", "(+)")
            self._advance("(+)")
            return True
        for terminator in sorted(self.config.statement_terminators, key=len, reverse=True):
            if terminator and remaining.startswith(terminator):
                self._emit(TokenKind.TERMINATOR, terminator, terminator)
                self._advance(terminator)
                return True
        operators = sorted(
            set(_OPERATORS) | set(self.config.extra_operators), key=len, reverse=True
        )
        for operator in operators:
            if remaining.startswith(operator):
                self._emit(TokenKind.OPERATOR, operator, operator)
                self._advance(operator)
                return True
        char = self.text[self._offset]
        if char in "+-*/%=<>!~|&^:":
            self._emit(TokenKind.OPERATOR, char, char)
            self._advance(char)
            return True
        if char in "(),.[]{}":
            self._emit(TokenKind.PUNCT, char, char)
            self._advance(char)
            return True
        return False

    def _emit_unknown(self) -> None:
        char = self.text[self._offset]
        line, col = self._position()
        self._emit(TokenKind.OPERATOR, char, char)
        self.issues.append(
            Issue(
                "warning",
                "lexer.unknown_character",
                f"Unrecognized character {char!r}.",
                Span(line, col, line, col + 1),
            )
        )
        self._advance(char)
