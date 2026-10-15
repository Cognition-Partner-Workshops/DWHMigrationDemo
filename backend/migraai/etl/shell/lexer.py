"""Shell lexer for POSIX/bash command lines and scripts.

Produces WORD tokens (with structured ``parts`` recording expansions),
operator tokens, NEWLINE separators and HEREDOC bodies. The lexer never
raises; malformed input is flagged on tokens and in ``issues``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from migraai.discovery import ast
from migraai.etl.python.common import span


class ShKind(StrEnum):
    WORD = "WORD"
    OP = "OP"
    NEWLINE = "NEWLINE"
    HEREDOC = "HEREDOC"
    EOF = "EOF"


@dataclass(slots=True)
class ShToken:
    kind: ShKind
    text: str
    value: str
    line: int
    col: int
    parts: list[tuple[str, Any]] = field(default_factory=list)
    unterminated: bool = False


_WORD_META = set(" \t\n;&|<>()")
_OPS = (
    "<<<",
    "<<-",
    "<<",
    "2>&1",
    ">&2",
    "&>>",
    "&>",
    ">>",
    ">&",
    "<&",
    ";;&",
    ";;",
    ";&",
    "&&",
    "||",
    ">",
    "<",
    "|",
    ";",
    "&",
    "(",
    ")",
)
_FD_RE = re.compile(r"\d+(?=[<>])")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


@dataclass(slots=True)
class _Heredoc:
    delimiter: str
    strip_tabs: bool
    line: int


class ShellLexer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens: list[ShToken] = []
        self.issues: list[ast.Issue] = []
        self._pos = 0
        self._line = 1
        self._col = 1
        self._heredocs: list[_Heredoc] = []
        self._pending_op = ""

    def tokenize(self) -> list[ShToken]:
        while self._pos < len(self.text):
            char = self.text[self._pos]
            if char in " \t":
                self._advance(char)
            elif char == "\\" and self._peek(1) == "\n":
                self._advance("\\\n")
            elif char == "\n":
                self._emit(ShKind.NEWLINE, "\n", "\n")
                self._advance("\n")
                self._read_heredocs()
            elif char == "#":
                self._line_comment()
            elif self._op(char):
                continue
            else:
                self._word()
        self._finish_heredocs()
        self.tokens.append(ShToken(ShKind.EOF, "", "", self._line, self._col))
        return self.tokens

    # -- scanning ------------------------------------------------------------

    def _peek(self, offset: int = 0) -> str:
        pos = self._pos + offset
        return self.text[pos] if pos < len(self.text) else ""

    def _advance(self, raw: str) -> None:
        newlines = raw.count("\n")
        if newlines:
            self._line += newlines
            self._col = len(raw.rsplit("\n", 1)[-1]) + 1
        else:
            self._col += len(raw)
        self._pos += len(raw)

    def _emit(self, kind: ShKind, text: str, value: str, **kw: Any) -> ShToken:
        token = ShToken(kind, text, value, self._line, self._col, **kw)
        self.tokens.append(token)
        return token

    def _line_comment(self) -> None:
        end = self.text.find("\n", self._pos)
        if end < 0:
            end = len(self.text)
        self._advance(self.text[self._pos : end])

    def _op(self, char: str) -> bool:
        if char in "()":
            self._emit(ShKind.OP, char, char)
            self._advance(char)
            return True
        fd = _FD_RE.match(self.text, self._pos)
        if fd is not None:
            after = self._pos + len(fd.group(0))
            for op in (">>&", ">>", ">&", ">", "<&", "<"):
                if self.text.startswith(op, after):
                    raw = fd.group(0) + op
                    if op in {
                        ">&",
                        "<&",
                    }:
                        target = re.match(r"[\w-]+", self.text[after + len(op) :])
                        if target:
                            raw += target.group(0)
                    self._emit(ShKind.OP, raw, raw)
                    self._advance(raw)
                    if op.startswith("<<"):
                        self._note_heredoc(op)
                    return True
        remaining = self.text[self._pos :]
        for op in _OPS:
            if remaining.startswith(op):
                self._emit(ShKind.OP, op, op)
                self._advance(op)
                if op.startswith("<<") and op != "<<<":
                    self._note_heredoc(op)
                return True
        if char in "{}" and self._brace_is_op():
            self._emit(ShKind.OP, char, char)
            self._advance(char)
            return True
        return False

    def _brace_is_op(self) -> bool:
        # { or } is an operator only if it stands alone as a word
        prev = self.text[self._pos - 1] if self._pos > 0 else "\n"
        nxt = self._peek(1)
        return prev in " \t\n;&|(" and nxt in " \t\n;&|)"

    def _note_heredoc(self, op: str) -> None:
        # The delimiter word follows; recorded when the next WORD is scanned.
        self._pending_op = op

    def _read_heredocs(self) -> None:
        while self._heredocs:
            heredoc = self._heredocs.pop(0)
            start_line = self._line
            body_lines: list[str] = []
            terminated = False
            while self._pos < len(self.text):
                end = self.text.find("\n", self._pos)
                if end < 0:
                    end = len(self.text)
                line_text = self.text[self._pos : end]
                check = line_text.lstrip("\t") if heredoc.strip_tabs else line_text
                if check == heredoc.delimiter:
                    self._advance(line_text)
                    if self._peek() == "\n":
                        self._advance("\n")
                    terminated = True
                    break
                body_lines.append(line_text.lstrip("\t") if heredoc.strip_tabs else line_text)
                self._advance(line_text)
                if self._peek() == "\n":
                    self._advance("\n")
            token = ShToken(
                ShKind.HEREDOC,
                "\n".join(body_lines),
                "\n".join(body_lines),
                start_line,
                1,
            )
            if not terminated:
                token.unterminated = True
                self.issues.append(
                    ast.Issue(
                        "error",
                        "shell.syntax",
                        f"Unterminated heredoc (expected {heredoc.delimiter!r}).",
                        span(heredoc.line, self._line),
                    )
                )
            self.tokens.append(token)

    def _finish_heredocs(self) -> None:
        for heredoc in self._heredocs:
            self.issues.append(
                ast.Issue(
                    "error",
                    "shell.syntax",
                    f"Heredoc {heredoc.delimiter!r} never terminated.",
                    span(heredoc.line),
                )
            )
        self._heredocs.clear()

    # -- words -----------------------------------------------------------------

    def _word(self) -> None:
        start_line, start_col = self._line, self._col
        start = self._pos
        parts: list[tuple[str, Any]] = []
        lit: list[str] = []
        unterminated = False

        def flush_lit() -> None:
            if lit:
                parts.append(("lit", "".join(lit)))
                lit.clear()

        while self._pos < len(self.text):
            char = self.text[self._pos]
            if char in _WORD_META or char in "{}" and self._brace_is_op():
                break
            if char == "\\":
                nxt = self._peek(1)
                if nxt == "\n":
                    self._advance("\\\n")
                    continue
                lit.append(nxt or "\\")
                self._advance(self.text[self._pos : self._pos + 2])
                continue
            if char == "'":
                flush_lit()
                if not self._single_quote(parts):
                    unterminated = True
                continue
            if char == '"':
                flush_lit()
                if not self._double_quote(parts):
                    unterminated = True
                continue
            if char == "`":
                flush_lit()
                if not self._backtick(parts):
                    unterminated = True
                continue
            if char == "$":
                flush_lit()
                if not self._dollar(parts, lit):
                    unterminated = True
                continue
            lit.append(char)
            self._advance(char)
        flush_lit()
        raw = self.text[start : self._pos]
        value = "".join(part[1] if part[0] == "lit" else "" for part in parts)
        if len(parts) == 0:
            parts = [("lit", raw)]
        token = ShToken(
            ShKind.WORD,
            raw,
            value if all(part[0] == "lit" for part in parts) else raw,
            start_line,
            start_col,
            parts=parts,
            unterminated=unterminated,
        )
        if unterminated:
            self.issues.append(
                ast.Issue(
                    "error",
                    "shell.syntax",
                    "Unterminated quote or expansion.",
                    span(start_line, self._line),
                )
            )
        self.tokens.append(token)
        if self._pending_op in {"<<", "<<-"}:
            delimiter = token.value.strip("'\"")
            self._heredocs.append(_Heredoc(delimiter, self._pending_op == "<<-", token.line))
            self._pending_op = ""

    def _single_quote(self, parts: list[tuple[str, Any]]) -> bool:
        self._advance("'")
        value: list[str] = []
        while self._pos < len(self.text):
            char = self.text[self._pos]
            if char == "'":
                self._advance("'")
                parts.append(("lit", "".join(value)))
                return True
            if char == "\n":
                # recovery: end the quote at the newline
                parts.append(("lit", "".join(value)))
                return False
            value.append(char)
            self._advance(char)
        parts.append(("lit", "".join(value)))
        return False

    def _double_quote(self, parts: list[tuple[str, Any]]) -> bool:
        self._advance('"')
        lit: list[str] = []

        def flush() -> None:
            if lit:
                parts.append(("lit", "".join(lit)))
                lit.clear()

        while self._pos < len(self.text):
            char = self.text[self._pos]
            if char == '"':
                self._advance('"')
                flush()
                return True
            if char == "\n":
                flush()
                return False
            if char == "\\":
                nxt = self._peek(1)
                if nxt in '"$`\\\n':
                    if nxt != "\n":
                        lit.append(nxt)
                    self._advance(self.text[self._pos : self._pos + 2])
                    continue
                lit.append(char)
                self._advance(char)
                continue
            if char == "`":
                flush()
                if not self._backtick(parts):
                    return False
                continue
            if char == "$":
                flush()
                if not self._dollar(parts, lit):
                    return False
                continue
            lit.append(char)
            self._advance(char)
        flush()
        return False

    def _backtick(self, parts: list[tuple[str, Any]]) -> bool:
        self._advance("`")
        start = self._pos
        start_line = self._line
        while self._pos < len(self.text):
            char = self.text[self._pos]
            if char == "`":
                inner = self.text[start : self._pos]
                parts.append(("subst", inner))
                self._advance("`")
                return True
            if char == "\\" and self._peek(1) in "`$\\":
                self._advance(self.text[self._pos : self._pos + 2])
                continue
            self._advance(char)
        parts.append(("subst", self.text[start : self._pos]))
        self.issues.append(
            ast.Issue("error", "shell.syntax", "Unterminated backtick.", span(start_line))
        )
        return False

    def _dollar(self, parts: list[tuple[str, Any]], lit: list[str]) -> bool:
        rest = self.text[self._pos :]
        if rest.startswith("$(("):
            end = rest.find("))", 3)
            if end < 0:
                lit.append("$")
                self._advance("$")
                return True
            parts.append(("arith", rest[3:end]))
            self._advance(rest[: end + 2])
            return True
        if rest.startswith("$("):
            depth = 0
            index = 1
            while index < len(rest):
                char = rest[index]
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        inner = rest[2:index]
                        parts.append(("subst", inner))
                        self._advance(rest[: index + 1])
                        return True
                elif char in "'\"":
                    close = rest.find(char, index + 1)
                    if close < 0:
                        break
                    index = close
                index += 1
            parts.append(("subst", rest[2:]))
            self._advance(rest)
            return False
        match = re.match(r"\$\{([^}]*)\}", rest)
        if match:
            parts.append(("param", match.group(1)))
            self._advance(match.group(0))
            return True
        match = _IDENT_RE.match(rest, 1)
        if match:
            parts.append(("var", match.group(0)))
            self._advance(rest[: match.end()])
            return True
        if len(rest) > 1 and rest[1] in "@*#?$!":
            parts.append(("var", rest[1]))
            self._advance(rest[:2])
            return True
        match = re.match(r"\$\d+", rest)
        if match:
            parts.append(("var", match.group(0)[1:]))
            self._advance(match.group(0))
            return True
        lit.append("$")
        self._advance("$")
        return True


def tokenize(text: str) -> tuple[list[ShToken], list[ast.Issue]]:
    lexer = ShellLexer(text)
    tokens = lexer.tokenize()
    return tokens, lexer.issues


__all__ = ["ShKind", "ShToken", "ShellLexer", "tokenize"]
