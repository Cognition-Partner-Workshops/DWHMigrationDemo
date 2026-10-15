"""Minimal Scala tokenizer + call-pattern extraction for ``.scala`` sources.

Not a full grammar: tokenizes comments, strings (incl. triple-quoted and
s/f/raw interpolators), chars, idents, numbers and punctuation, then matches
Spark call patterns on the token sequence. Objects: ``scala_object``
(``<package>.<Name>``) and ``function`` (``<package>.<Name>.<def>``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from migraai.discovery import ast
from migraai.etl.python.common import (
    EtlBuilder,
    redact,
    span,
)


class ScKind(StrEnum):
    IDENT = "IDENT"
    STRING = "STRING"
    NUMBER = "NUMBER"
    CHAR = "CHAR"
    PUNCT = "PUNCT"
    NEWLINE = "NEWLINE"
    EOF = "EOF"


@dataclass(slots=True)
class ScToken:
    kind: ScKind
    text: str
    line: int


_IDENT = re.compile(r"[A-Za-z_][\w$]*")


def tokenize_scala(text: str) -> list[ScToken]:
    tokens: list[ScToken] = []
    pos = 0
    line = 1
    n = len(text)
    while pos < n:
        char = text[pos]
        if char == "\n":
            tokens.append(ScToken(ScKind.NEWLINE, "\n", line))
            line += 1
            pos += 1
            continue
        if char in " \t":
            pos += 1
            continue
        if text.startswith("//", pos):
            end = text.find("\n", pos)
            pos = end if end >= 0 else n
            continue
        if text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            end = end + 2 if end >= 0 else n
            line += text.count("\n", pos, end)
            pos = end
            continue
        if text.startswith('"""', pos) or (char in "sfr" and text.startswith('"""', pos + 1)):
            start = pos + 1 if char in "sfr" else pos
            end = text.find('"""', start + 3)
            end = end + 3 if end >= 0 else n
            raw = text[start:end]
            tokens.append(ScToken(ScKind.STRING, raw[3:-3], line))
            line += raw.count("\n")
            pos = end
            continue
        if char == '"' or (char in "sfr" and text.startswith('"', pos + 1)):
            start_line = line
            index = pos + 1 if char in "sfr" else pos
            index += 1
            value: list[str] = []
            while index < n and text[index] != '"':
                if text[index] == "\\" and index + 1 < n:
                    value.append(text[index + 1])
                    index += 2
                    continue
                value.append(text[index])
                index += 1
            tokens.append(ScToken(ScKind.STRING, "".join(value), start_line))
            pos = index + 1
            continue
        if char == "'":
            match = re.match(r"'(\\.|[^'\\])'", text[pos:])
            if match:
                tokens.append(ScToken(ScKind.CHAR, match.group(0), line))
                pos += len(match.group(0))
                continue
        match = _IDENT.match(text, pos)
        if match:
            tokens.append(ScToken(ScKind.IDENT, match.group(0), line))
            pos = match.end()
            continue
        match = re.match(r"\d+(?:\.\d+)?[LlFfDd]?", text[pos:])
        if match:
            tokens.append(ScToken(ScKind.NUMBER, match.group(0), line))
            pos += match.end()
            continue
        tokens.append(ScToken(ScKind.PUNCT, char, line))
        pos += 1
    tokens.append(ScToken(ScKind.EOF, "", line))
    return tokens


_READ_TABLE_METHODS = {"table"}
_WRITE_TABLE_METHODS = {"saveAsTable", "insertInto", "writeTo"}
_PATH_READS = {
    "parquet",
    "csv",
    "json",
    "orc",
    "textFile",
    "load",
    "avro",
    "sequenceFile",
    "objectFile",
}
_PATH_WRITES = {"save", "saveAsTextFile"}


def _literal_arg(tokens: list[ScToken], index: int) -> tuple[str | None, int]:
    """If tokens[index:] is ``( "literal"`` return (value, index after it)."""
    if index < len(tokens) and tokens[index].text == "(":
        index += 1
        while index < len(tokens) and tokens[index].kind is ScKind.NEWLINE:
            index += 1
        if index < len(tokens) and tokens[index].kind is ScKind.STRING:
            return tokens[index].text, index + 1
    return None, index


def _chain_has(tokens: list[ScToken], index: int, word: str, back: int = 40) -> bool:
    for back_index in range(index - 1, max(index - back, -1), -1):
        token = tokens[back_index]
        if token.kind is ScKind.IDENT and token.text == word:
            return True
        if token.kind is ScKind.PUNCT and token.text in {";", "{", "}"}:
            return False
        if token.kind is ScKind.NEWLINE:
            # a chain may continue on the next line only via a leading `.`
            next_token = tokens[back_index + 1] if back_index + 1 < len(tokens) else None
            if not (next_token is not None and next_token.text == "."):
                return False
    return False


def parse_scala(text: str, builder: EtlBuilder) -> None:
    tokens = tokenize_scala(text)
    package = ""
    objects: list[tuple[str, int, int]] = []  # (name, start_index, brace_depth)
    for index, token in enumerate(tokens):
        if token.kind is ScKind.IDENT and token.text == "package":
            names: list[str] = []
            cursor = index + 1
            while cursor < len(tokens) and (
                tokens[cursor].kind is ScKind.IDENT or tokens[cursor].text == "."
            ):
                if tokens[cursor].kind is ScKind.IDENT:
                    names.append(tokens[cursor].text)
                cursor += 1
            package = ".".join(names)
        if token.kind is ScKind.IDENT and token.text in {"object", "class", "trait"}:
            nxt = _next_non_newline(tokens, index + 1)
            if nxt is not None and tokens[nxt].kind is ScKind.IDENT:
                objects.append((tokens[nxt].text, index, nxt))
    _emit_objects(tokens, package, objects, builder)


def _next_non_newline(tokens: list[ScToken], index: int) -> int | None:
    while index < len(tokens):
        if tokens[index].kind is ScKind.NEWLINE:
            index += 1
            continue
        return index
    return None


def _emit_objects(
    tokens: list[ScToken],
    package: str,
    objects: list[tuple[str, int, int]],
    builder: EtlBuilder,
) -> None:
    for name, start, _ in objects:
        qualified = f"{package}.{name}" if package else name
        # find matching braces for the object body
        body_start = None
        depth = 0
        for cursor in range(start, len(tokens)):
            if tokens[cursor].text == "{":
                depth += 1
                if body_start is None:
                    body_start = cursor
            elif tokens[cursor].text == "}":
                depth -= 1
                if depth == 0 and body_start is not None:
                    body_end = cursor
                    break
        else:
            body_end = len(tokens)
        body = tokens[(body_start or start) : body_end]
        functions = _scala_functions(body, qualified)
        skip: set[int] = set()
        for _, fn_start, fn_end in functions:
            skip.update(range(fn_start, fn_end))
        refs: list[ast.RefHint] = []
        embedded: list[tuple[str, int]] = []  # (sql, line)
        _scala_invocations(body, qualified, qualified, refs, embedded, builder, skip)
        node = ast.DialectStatement(
            name="spark.scala_object",
            args={
                "name": ast.Identifier(value=name),
                "package": ast.Identifier(value=package),
            },
            references=refs,
            span=span(tokens[start].line, tokens[min(body_end, len(tokens) - 1)].line),
        )
        for sql, _line in embedded:
            builder.embed(qualified, sql, "scala")
        builder.add(
            "scala_object",
            qualified,
            node,
            tokens[start].line,
            tokens[min(body_end, len(tokens) - 1)].line,
        )
        for fn_name, fn_start, fn_end in functions:
            fn_body = body[fn_start:fn_end]
            fn_refs: list[ast.RefHint] = []
            fn_embedded: list[tuple[str, int]] = []
            fn_q = f"{qualified}.{fn_name}"
            _scala_invocations(fn_body, fn_q, qualified, fn_refs, fn_embedded, builder)
            fn_node = ast.DialectStatement(
                name="spark.scala_function",
                args={"name": ast.Identifier(value=fn_name)},
                references=fn_refs,
                span=span(body[fn_start].line, body[min(fn_end, len(body)) - 1].line),
            )
            for sql, _line in fn_embedded:
                builder.embed(fn_q, sql, "scala")
            builder.add(
                "function",
                fn_q,
                fn_node,
                body[fn_start].line,
                body[min(fn_end, len(body)) - 1].line,
            )


def _scala_functions(tokens: list[ScToken], qualified: str) -> list[tuple[str, int, int]]:
    functions: list[tuple[str, int, int]] = []
    for index, token in enumerate(tokens):
        if token.kind is ScKind.IDENT and token.text == "def":
            nxt = _next_non_newline(tokens, index + 1)
            if nxt is None or tokens[nxt].kind is not ScKind.IDENT:
                continue
            # find body start '{' or '=' at depth 0 after signature
            depth = 0
            body_start = None
            cursor = nxt
            while cursor < len(tokens):
                t = tokens[cursor]
                if t.text == "(" or t.text == "[":
                    depth += 1
                elif t.text == ")" or t.text == "]":
                    depth -= 1
                elif depth == 0 and t.text == "=":
                    body_start = cursor
                    break
                elif depth == 0 and t.text == "{":
                    body_start = cursor
                    break
                elif (
                    depth == 0
                    and t.kind is ScKind.NEWLINE
                    and cursor + 1 < len(tokens)
                    and tokens[cursor + 1].text == "def"
                ):
                    break
                elif depth == 0 and t.text == ";":
                    break
                cursor += 1
            end = len(tokens)
            if body_start is not None and tokens[body_start].text == "{":
                depth = 1
                cursor = body_start + 1
                while cursor < len(tokens) and depth:
                    if tokens[cursor].text == "{":
                        depth += 1
                    elif tokens[cursor].text == "}":
                        depth -= 1
                    cursor += 1
                end = cursor
            functions.append((tokens[nxt].text, index, end))
    return functions


def _scala_invocations(
    tokens: list[ScToken],
    owner: str,
    object_q: str,
    refs: list[ast.RefHint],
    embedded: list[tuple[str, int]],
    builder: EtlBuilder,
    skip: set[int] | None = None,
) -> None:
    index = 0
    while index < len(tokens):
        if skip and index in skip:
            index += 1
            continue
        token = tokens[index]
        index += 1
        if token.kind is not ScKind.IDENT:
            continue
        sp = span(token.line)
        text = token.text
        if text == "sql":
            value, _ = _literal_arg(tokens, index)
            if value is not None:
                embedded.append((redact(value), token.line))
        elif text in _READ_TABLE_METHODS:
            value, _ = _literal_arg(tokens, index)
            if value:
                refs.append(builder.ref(value, "etl_reads", sp))
        elif text in _WRITE_TABLE_METHODS:
            value, _ = _literal_arg(tokens, index)
            if value:
                refs.append(builder.ref(value, "etl_writes", sp))
        elif text == "forName":
            # DeltaTable.forName(spark, "t") — first string arg is the table
            cursor = index
            if cursor < len(tokens) and tokens[cursor].text == "(":
                cursor += 1
                while cursor < len(tokens) and tokens[cursor].text != ")":
                    if tokens[cursor].kind is ScKind.STRING:
                        refs.append(builder.ref(tokens[cursor].text, "etl_reads", sp))
                        break
                    cursor += 1
        elif text in _PATH_READS | _PATH_WRITES:
            value, _ = _literal_arg(tokens, index)
            if value:
                write = text in _PATH_WRITES or _chain_has(tokens, index, "write")
                refs.append(
                    builder.ref(
                        value,
                        "etl_writes" if write else "etl_reads",
                        sp,
                        split=False,
                    )
                )
        elif text == "option":
            value, nxt = _literal_arg(tokens, index)
            if value in {"dbtable", "query", "url"}:
                # option("dbtable", "x.y")
                cursor = index
                args: list[str] = []
                if cursor < len(tokens) and tokens[cursor].text == "(":
                    cursor += 1
                    while cursor < len(tokens) and tokens[cursor].text != ")":
                        if tokens[cursor].kind is ScKind.STRING:
                            args.append(tokens[cursor].text)
                        cursor += 1
                if len(args) >= 2:
                    if args[0] == "dbtable":
                        write = _chain_has(tokens, index, "write")
                        refs.append(
                            builder.ref(args[1], "etl_writes" if write else "etl_reads", sp)
                        )
                    elif args[0] == "query":
                        embedded.append((redact(args[1]), token.line))
        elif text == "jdbc":
            cursor = index
            args = []
            if cursor < len(tokens) and tokens[cursor].text == "(":
                cursor += 1
                while cursor < len(tokens) and tokens[cursor].text != ")":
                    if tokens[cursor].kind is ScKind.STRING:
                        args.append(tokens[cursor].text)
                    cursor += 1
            if len(args) >= 2:
                write = _chain_has(tokens, index, "write")
                refs.append(builder.ref(args[1], "etl_writes" if write else "etl_reads", sp))


__all__ = ["parse_scala", "tokenize_scala"]
