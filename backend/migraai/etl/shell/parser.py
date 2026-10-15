"""Shell script parser: tokens -> AST statements.

Grammar (design §7):
  script    := sequence
  sequence  := and_or ((';' | '&' | NEWLINE) and_or)*
  and_or    := pipeline (('&&' | '||') pipeline)*
  pipeline  := '!'? command ('|' command)*
  command   := simple | if | for | while | until | case | function | group |
               unsupported
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

from migraai.discovery import ast
from migraai.etl.python.common import redact, span
from migraai.etl.shell.lexer import ShKind, ShToken, tokenize

_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
_FUNC_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9-]*$")
_ARITH_TOKEN_RE = re.compile(r"\d+|[A-Za-z_][A-Za-z_0-9]*|[+\-*/%()]")


class ShellScript:
    """Parsed script: statements plus top-level constant variable assignments."""

    def __init__(
        self,
        statements: list[ast.Node],
        variables: dict[str, str],
        issues: list[ast.Issue] | None = None,
    ) -> None:
        self.statements = statements
        self.variables = variables
        self.issues = issues or []


class ShellParser:
    def __init__(self, tokens: list[ShToken], issues: list[ast.Issue]) -> None:
        self.tokens = tokens
        self.index = 0
        self.issues = issues
        self._heredoc_queue: list[ShToken] = []

    # -- cursor --------------------------------------------------------------

    def peek(self, offset: int = 0) -> ShToken:
        return self.tokens[min(self.index + offset, len(self.tokens) - 1)]

    def advance(self) -> ShToken:
        token = self.peek()
        if token.kind is not ShKind.EOF:
            self.index += 1
        return token

    def at_op(self, *ops: str) -> bool:
        token = self.peek()
        return token.kind is ShKind.OP and token.value in ops

    def at_word(self, *words: str) -> bool:
        token = self.peek()
        return token.kind is ShKind.WORD and token.value.casefold() in {
            word.casefold() for word in words
        }

    def accept_op(self, *ops: str) -> ShToken | None:
        return self.advance() if self.at_op(*ops) else None

    def skip_separators(self) -> None:
        while self.peek().kind is ShKind.NEWLINE or self.at_op(";"):
            self.advance()

    # -- entry -----------------------------------------------------------------

    def parse(self) -> list[ast.Node]:
        statements: list[ast.Node] = []
        self.skip_separators()
        while self.peek().kind is not ShKind.EOF:
            stmt = self.parse_and_or()
            if stmt is not None:
                statements.append(stmt)
            if self.peek().kind is ShKind.HEREDOC:
                # stray heredoc body (redirect already consumed); attach later
                self.advance()
                continue
            if self.peek().kind is ShKind.NEWLINE or self.at_op(";"):
                self.skip_separators()
                continue
            if self.accept_op("&"):
                if statements:
                    _set_background(statements[-1])
                self.skip_separators()
                continue
            if self.peek().kind is ShKind.EOF:
                break
            # unexpected token: skip to next separator to keep going
            token = self.advance()
            if token.kind is not ShKind.EOF and token.kind is not ShKind.HEREDOC:
                self.issues.append(
                    ast.Issue(
                        "error",
                        "shell.syntax",
                        f"Unexpected token {token.text!r}.",
                        span(token.line),
                    )
                )
        return statements

    # -- grammar ---------------------------------------------------------------

    def parse_and_or(self) -> ast.Node | None:
        left = self.parse_pipeline()
        if left is None:
            return None
        while self.at_op("&&", "||"):
            op = self.advance()
            right = self.parse_pipeline()
            if right is None:
                right = ast.Unparsed(
                    text="", reason=f"Missing command after {op.text}.", span=span(op.line)
                )
            left = ast.DialectStatement(
                name="shell.and_or",
                args={
                    "op": ast.Literal(type="string", value=op.value),
                    "left": left,
                    "right": right,
                },
                references=[],
                span=span(left.span.start_line, right.span.end_line),
            )
        return left

    def parse_pipeline(self) -> ast.Node | None:
        negated = self.at_word("!")
        if negated:
            self.advance()
        first = self.parse_command()
        if first is None:
            if negated:
                self.issues.append(
                    ast.Issue(
                        "error", "shell.syntax", "'!' without command.", span(self.peek().line)
                    )
                )
            return None
        commands = [first]
        while self.at_op("|"):
            self.advance()
            nxt = self.parse_command()
            if nxt is None:
                nxt = ast.Unparsed(
                    text="", reason="Missing command after '|'.", span=span(self.peek().line)
                )
            commands.append(nxt)
        if len(commands) == 1 and not negated:
            return first
        return ast.DialectStatement(
            name="shell.pipeline",
            args={
                "commands": commands,
                "negated": ast.Literal(type="boolean", value="True") if negated else None,
            },
            span=span(commands[0].span.start_line, commands[-1].span.end_line),
        )

    def parse_command(self) -> ast.Node | None:
        token = self.peek()
        if token.kind is ShKind.WORD:
            word = token.value.casefold()
            if word == "if":
                return self.parse_if()
            if word == "for":
                return self.parse_for()
            if word in {"while", "until"}:
                return self.parse_while(until=word == "until")
            if word == "case":
                return self.parse_case()
            if word == "function":
                return self.parse_function()
            if word in {"select", "coproc", "eval"}:
                return self.parse_unsupported(word)
            if word in {"then", "elif", "else", "fi", "do", "done", "esac", "in"}:
                return None
            if (
                _FUNC_NAME_RE.match(token.value)
                and self.peek(1).kind is ShKind.OP
                and self.peek(1).value == "("
                and self.peek(2).kind is ShKind.OP
                and self.peek(2).value == ")"
            ):
                start = self.advance()
                name = ast.Identifier(value=start.value, span=span(start.line))
                self.advance()
                self.advance()
                return self._function_body(name, start)
        if token.kind is ShKind.OP:
            if token.value == "(":
                return self.parse_group(subshell=True)
            if token.value == "{":
                return self.parse_group(subshell=False)
            if token.value in {")", "}"}:
                return None
        return self.parse_simple()

    # -- simple commands ---------------------------------------------------------

    def parse_simple(self) -> ast.Node | None:
        start = self.peek()
        if start.kind not in {ShKind.WORD, ShKind.OP}:
            return None
        assignments: list[ast.Node] = []
        words: list[ast.Node] = []
        redirects: list[ast.Node] = []
        saw_word = False
        builtin_assign = False
        while True:
            token = self.peek()
            if token.kind is ShKind.WORD:
                word_node = self._word_node(token)
                raw = token.value
                if not saw_word and _ASSIGN_RE.match(raw):
                    assignments.append(self._assign_node(token))
                    self.advance()
                    continue
                if (
                    not saw_word
                    and not assignments
                    and raw.casefold() in {"export", "readonly", "local", "declare", "typeset"}
                ):
                    builtin_assign = True
                    self.advance()
                    continue
                if builtin_assign and _ASSIGN_RE.match(raw):
                    assignments.append(self._assign_node(token, exported=True))
                    self.advance()
                    continue
                if builtin_assign and _FUNC_NAME_RE.match(raw):
                    assignments.append(
                        ast.Assign(
                            target=ast.Variable(name=raw, span=span(token.line, token.line)),
                            value=None,
                            span=span(token.line, token.line),
                        )
                    )
                    self.advance()
                    continue
                words.append(word_node)
                saw_word = True
                self.advance()
                continue
            if token.kind is ShKind.OP and (
                token.value.startswith(">")
                or token.value.startswith("<")
                or re.match(r"^\d*[<>]", token.value)
                or token.value in {"&>", "&>>", ">&", "<&", "2>&1", ">&2"}
            ):
                redirects.append(self._redirect())
                continue
            break
        if not assignments and not words:
            return None
        program: ast.Node | None = None
        args: list[ast.Node] = []
        if words:
            program = words[0]
            args = words[1:]
            args = _mask_secret_flags(program, args)
            if isinstance(program, ast.Literal):
                base = posixpath.basename(program.value)
                program = ast.Identifier(value=base, span=program.span)
        node = ast.DialectStatement(
            name="shell.command",
            args={
                "program": program,
                "args": args,
                "redirects": redirects,
                "assignments": assignments,
            },
            span=span(start.line, self.tokens[max(self.index - 1, 0)].line),
        )
        return node

    def _assign_node(self, token: ShToken, exported: bool = False) -> ast.Assign:
        name, _, _ = token.value.partition("=")
        if _SECRET_NAME_RE.search(name):
            value_node = ast.Literal(type="string", value="****", span=span(token.line))
        else:
            # find the value portion inside the token parts (after first '=')
            value_parts = self._value_parts(token)
            value_node = _parts_word(value_parts, token.line)
        return ast.Assign(
            target=ast.Variable(name=name, span=span(token.line, token.line)),
            value=value_node,
            span=span(token.line, token.line),
        )

    def _value_parts(self, token: ShToken) -> list[tuple[str, Any]]:
        raw_parts = token.parts
        # parts do not know the '=' split; rebuild by walking the raw text
        text = token.text
        eq = text.find("=")
        if eq < 0:
            return [("lit", "")]
        consumed = 0
        value_parts: list[tuple[str, Any]] = []
        lit_buf = ""
        for kind, payload in raw_parts:
            length = _part_raw_length(kind, payload)
            if consumed + length <= eq + 1:
                consumed += length
                continue
            if consumed < eq + 1:
                # part straddles the '='
                offset = eq + 1 - consumed
                lit_buf += str(payload)[offset:]
                consumed += length
                continue
            if lit_buf:
                value_parts.append(("lit", lit_buf))
                lit_buf = ""
            value_parts.append((kind, payload))
            consumed += length
        if lit_buf or not value_parts:
            value_parts.append(("lit", lit_buf))
        return value_parts

    def _redirect(self) -> ast.Node:
        op = self.advance()
        heredoc_node: ast.Node | None = None
        target: ast.Node | None = None
        token = self.peek()
        if token.kind is ShKind.WORD:
            target = self._word_node(token)
            self.advance()
        if op.value.startswith("<<") and op.value != "<<<":
            # consume matching HEREDOC token (lexer queues them after newline)
            body = self._take_heredoc()
            heredoc_node = ast.Literal(type="string", value=redact(body), span=span(op.line))
        return ast.DialectExpr(
            name="shell.redirect",
            args={"op": op.value, "target": target, "heredoc": heredoc_node},
            span=span(op.line),
        )

    def _take_heredoc(self) -> str:
        ahead = self.index
        while ahead < len(self.tokens):
            token = self.tokens[ahead]
            if token.kind is ShKind.HEREDOC:
                del self.tokens[ahead]
                if self.index > ahead:
                    self.index -= 1
                return token.value
            if token.kind is ShKind.NEWLINE or token.kind is ShKind.EOF:
                ahead += 1
                continue
            ahead += 1
        return ""

    # -- compound commands ------------------------------------------------------

    def parse_if(self) -> ast.Node:
        start = self.advance()  # 'if'
        branches: list[tuple[ast.Node, list[ast.Node]]] = []
        else_: list[ast.Node] = []
        closed = False
        while True:
            cond = self._parse_sequence_until({"then"})
            if not self.at_word("then"):
                break
            self.advance()
            body = self._parse_sequence_until({"elif", "else", "fi"})
            branches.append((_as_condition(cond), body))
            if self.at_word("elif"):
                self.advance()
                continue
            if self.at_word("else"):
                self.advance()
                else_ = self._parse_sequence_until({"fi"})
            if self.at_word("fi"):
                self.advance()
                closed = True
            break
        if not closed:
            return self._unterminated(start, "if", "fi")
        return ast.If(
            branches=branches,
            else_=else_,
            span=span(start.line, self.tokens[max(self.index - 1, 0)].line),
        )

    def parse_for(self) -> ast.Node:
        start = self.advance()  # 'for'
        var_token = self.peek()
        var = ast.Identifier(value="", span=span(start.line))
        if var_token.kind is ShKind.WORD:
            var = ast.Identifier(value=var_token.value, span=span(var_token.line))
            self.advance()
        words: list[ast.Node] = []
        if self.at_word("in"):
            self.advance()
            while self.peek().kind is ShKind.WORD:
                words.append(self._word_node(self.advance()))
        self._consume_do()
        body = self._parse_sequence_until({"done"})
        if self.at_word("done"):
            self.advance()
        else:
            return self._unterminated(start, "for", "done")
        iterable: ast.Node = (
            ast.ArrayLiteral(items=words)
            if words
            else ast.Variable(name="@", span=span(start.line))
        )
        return ast.ForLoop(
            var=var,
            iterable=iterable,
            statements=body,
            span=span(start.line, self.tokens[max(self.index - 1, 0)].line),
        )

    def parse_while(self, *, until: bool) -> ast.Node:
        start = self.advance()
        cond = self._parse_sequence_until({"do"})
        self._consume_do()
        body = self._parse_sequence_until({"done"})
        if self.at_word("done"):
            self.advance()
        else:
            return self._unterminated(start, "until" if until else "while", "done")
        condition = _as_condition(cond)
        if until:
            condition = ast.Unary(op="not", operand=condition, span=condition.span)
        return ast.While(
            condition=condition,
            statements=body,
            span=span(start.line, self.tokens[max(self.index - 1, 0)].line),
        )

    def parse_case(self) -> ast.Node:
        start = self.advance()  # 'case'
        subject = ast.Literal(type="string", value="", span=span(start.line))
        if self.peek().kind is ShKind.WORD:
            subject = self._word_node(self.advance())
        if not self.at_word("in"):
            return self._unterminated(start, "case", "esac")
        self.advance()
        arms: list[ast.Node] = []
        self.skip_separators()
        while not self.at_word("esac") and self.peek().kind is not ShKind.EOF:
            if self.at_op("("):
                self.advance()
            patterns: list[ast.Node] = []
            while self.peek().kind is ShKind.WORD or self.at_op("|"):
                if self.at_op("|"):
                    self.advance()
                    continue
                patterns.append(self._word_node(self.advance()))
            if not self.at_op(")"):
                break
            self.advance()
            body = self._parse_sequence_until({"esac"}, stop_ops={";;", ";&", ";;&"})
            self.accept_op(";;", ";&", ";;&")
            arms.append(
                ast.DialectExpr(
                    name="shell.case_arm",
                    args={"patterns": patterns, "statements": body},
                    span=span(start.line),
                )
            )
            self.skip_separators()
        if self.at_word("esac"):
            self.advance()
        else:
            return self._unterminated(start, "case", "esac")
        return ast.DialectStatement(
            name="shell.case",
            args={"subject": subject, "arms": arms},
            span=span(start.line, self.tokens[max(self.index - 1, 0)].line),
        )

    def parse_function(self) -> ast.Node | None:
        start = self.advance()  # 'function'
        name = ast.Identifier(value="", span=span(start.line))
        if self.peek().kind is ShKind.WORD:
            name = ast.Identifier(value=self.peek().value, span=span(self.peek().line))
            self.advance()
        if self.at_op("("):
            self.advance()
            self.accept_op(")")
        return self._function_body(name, start)

    def _function_body(self, name: ast.Identifier, start: ShToken) -> ast.Node | None:
        self.skip_separators()
        if self.at_op("{"):
            self.advance()
            body = self._parse_sequence_until(set(), stop_ops={"}"})
            if self.at_op("}"):
                self.advance()
            else:
                self._resync_function_body()
                return self._unterminated(start, "function", "}")
        elif self.at_op("("):
            self.advance()
            body = self._parse_sequence_until(set(), stop_ops={")"})
            if self.at_op(")"):
                self.advance()
            else:
                self._resync_function_body()
                return self._unterminated(start, "function", ")")
        else:
            self.issues.append(
                ast.Issue(
                    "error",
                    "shell.syntax",
                    "Expected function body.",
                    span(self.peek().line),
                )
            )
            return None
        return ast.DialectStatement(
            name="shell.function",
            args={"name": name, "body": body},
            span=span(start.line, self.tokens[max(self.index - 1, 0)].line),
        )

    def parse_group(self, *, subshell: bool) -> ast.Node | None:
        start = self.advance()  # '(' or '{'
        closer = ")" if subshell else "}"
        body = self._parse_sequence_until(set(), stop_ops={closer})
        if self.at_op(closer):
            self.advance()
        else:
            return self._unterminated(start, "group", closer)
        return ast.DialectStatement(
            name="shell.group",
            args={
                "statements": body,
                "subshell": ast.Literal(type="boolean", value="true" if subshell else "false"),
            },
            span=span(start.line, self.tokens[max(self.index - 1, 0)].line),
        )

    def parse_unsupported(self, keyword: str) -> ast.Node:
        start = self.advance()
        words: list[ast.Node] = []
        while self.peek().kind is ShKind.WORD:
            words.append(self._word_node(self.advance()))
        self.issues.append(
            ast.Issue(
                "warning",
                "shell.unsupported_construct",
                f"Unsupported shell construct {keyword!r}.",
                span(start.line),
            )
        )
        return ast.DialectStatement(
            name="shell.unsupported",
            args={"keyword": ast.Literal(type="string", value=keyword), "words": words},
            span=span(start.line, self.tokens[max(self.index - 1, 0)].line),
        )

    # -- internals ----------------------------------------------------------------

    def _consume_do(self) -> None:
        self.skip_separators()
        if self.at_op(";"):
            self.advance()
        self.skip_separators()
        self.accept_word("do")
        self.skip_separators()

    def accept_word(self, word: str) -> ShToken | None:
        if self.at_word(word):
            return self.advance()
        return None

    def _parse_sequence_until(
        self, stop_words: set[str], stop_ops: set[str] | None = None
    ) -> list[ast.Node]:
        stop_ops = stop_ops or set()
        statements: list[ast.Node] = []
        self.skip_separators()
        while self.peek().kind is not ShKind.EOF:
            if self.at_word(*stop_words) if stop_words else False:
                break
            if self.at_op(*stop_ops) if stop_ops else False:
                break
            if self.at_op(")"):
                break
            stmt = self.parse_and_or()
            if stmt is None:
                break
            statements.append(stmt)
            if self.at_word(*stop_words) if stop_words else False:
                break
            if self.at_op(*stop_ops) if stop_ops else False:
                break
            if self.peek().kind in {ShKind.NEWLINE} or self.at_op(";", "&"):
                self.advance()
                self.skip_separators()
                continue
            if self.peek().kind is ShKind.EOF:
                break
            if self.peek().kind is ShKind.HEREDOC:
                self.advance()
                continue
            if self.peek().kind is ShKind.OP and self.peek().value == "{":
                # a `{` may start a group after e.g. `coproc name { …; }`
                continue
            break
        return statements

    def _resync_function_body(self) -> None:
        """After a syntax error inside a function body, resync at the closing
        ``}`` at column 1, or at the next col-1 ``name()``/``function name``
        definition, whichever comes first."""
        while self.peek().kind is not ShKind.EOF:
            token = self.peek()
            if token.kind is ShKind.OP and token.value == "}" and token.col == 1:
                return
            if token.kind is ShKind.WORD and token.col == 1:
                if token.value.casefold() == "function":
                    return
                if (
                    _FUNC_NAME_RE.match(token.value)
                    and self.peek(1).kind is ShKind.OP
                    and self.peek(1).value == "("
                    and self.peek(2).kind is ShKind.OP
                    and self.peek(2).value == ")"
                ):
                    return
            self.advance()

    def _unterminated(self, start: ShToken, construct: str, closer: str) -> ast.Unparsed:
        self.issues.append(
            ast.Issue(
                "error",
                "shell.syntax",
                f"Missing {closer!r} for {construct!r} starting on line {start.line}.",
                span(start.line),
            )
        )
        return ast.Unparsed(
            text=redact(f"{construct} block starting at line {start.line}"),
            reason=f"Missing {closer!r}.",
            span=span(start.line, self.peek().line),
        )

    def _word_node(self, token: ShToken) -> ast.Node:
        if token.unterminated:
            return ast.Unparsed(
                text=redact(token.text),
                reason="Unterminated quote or expansion.",
                span=span(token.line),
            )
        return _parts_word(token.parts, token.line)


def _part_raw_length(kind: str, payload: Any) -> int:
    if kind == "lit":
        return len(str(payload))
    if kind == "var":
        return len(str(payload)) + 1
    if kind == "param":
        return len(str(payload)) + 3
    if kind == "subst":
        return len(str(payload)) + 3
    if kind == "arith":
        return len(str(payload)) + 5
    return len(str(payload))


_SECRET_FLAG_TOOLS = {
    "bteq",
    "sqlplus",
    "bcp",
    "sqlcmd",
    "sqlldr",
    "nzsql",
    "nzload",
    "db2",
    "beeline",
    "hive",
    "snowsql",
    "mysql",
    "mysqldump",
    "mysqladmin",
}
_SECRET_FLAGS = {"-p", "-pw", "-w", "--password", "--passwd"}
_SECRET_NAME_RE = re.compile(r"password|passwd|pwd|secret|token|api_key", re.IGNORECASE)
# -P is only a secret flag for bcp/sqlcmd-style tools
_SECRET_FLAGS_P = {"bcp", "sqlcmd"}


def _mask_secret_flags(program: ast.Node, args: list[ast.Node]) -> list[ast.Node]:
    if not isinstance(program, ast.Literal):
        return args
    base = posixpath.basename(program.value).casefold()
    flags: set[str] = set()
    if base in _SECRET_FLAG_TOOLS:
        flags |= _SECRET_FLAGS
        if base in _SECRET_FLAGS_P:
            flags.add("-P")
    # generic long flags whose name looks secret (`--password`, `--api-token`…)
    masked_generic = [
        isinstance(a, ast.Literal)
        and a.value.startswith("--")
        and _SECRET_NAME_RE.search(a.value.split("=", 1)[0][2:])
        and "=" not in a.value
        for a in args
    ]
    if not flags and not any(masked_generic):
        return args
    flags |= {a.value for a, hit in zip(args, masked_generic, strict=False) if hit}
    masked: list[ast.Node] = []
    hide_next = False
    for arg in args:
        if (
            hide_next
            and isinstance(arg, ast.Literal)
            and arg.type == "string"
            and not arg.value.startswith("-")
        ):
            masked.append(ast.Literal(type="string", value="****", span=arg.span))
            hide_next = False
            continue
        hide_next = isinstance(arg, ast.Literal) and arg.value in flags
        if (
            base in _SECRET_FLAG_TOOLS
            and isinstance(arg, ast.Literal)
            and arg.type == "string"
            and arg.value
        ):
            value = arg.value
            if base.startswith("mysql") and re.match(r"-p\S+", value):
                masked.append(ast.Literal(type="string", value="-p****", span=arg.span))
                continue
            cleaned = redact(value)
            if base == "db2":
                cleaned = re.sub(r"(?i)(\busing\s+)\S+", r"\1****", cleaned)
            if cleaned != value:
                masked.append(ast.Literal(type="string", value=cleaned, span=arg.span))
                continue
        masked.append(arg)
    return masked


def _parts_word(parts: list[tuple[str, Any]], line: int) -> ast.Node:
    sp = span(line)
    nodes = [_part_node(kind, payload, line) for kind, payload in parts]
    if len(nodes) == 1:
        return nodes[0]
    return ast.DialectExpr(name="shell.word", args={"parts": nodes}, span=sp)


def _part_node(kind: str, payload: Any, line: int) -> ast.Node:
    sp = span(line)
    if kind == "lit":
        return ast.Literal(type="string", value=redact(str(payload)), span=sp)
    if kind == "var":
        return ast.Variable(name=str(payload), span=sp)
    if kind == "param":
        text = str(payload)
        name, _, rest = text.partition(":")
        name = name.strip()
        op = (":" + rest[:1]) if rest else ""
        default = rest[1:] if rest else ""
        return ast.DialectExpr(
            name="shell.param_expansion",
            args={
                "name": ast.Identifier(value=name),
                "op": ast.Literal(type="string", value=op),
                "default": ast.Literal(type="string", value=redact(default)),
            },
            span=sp,
        )
    if kind == "subst":
        inner_tokens, inner_issues = tokenize(str(payload))
        inner_parser = ShellParser(inner_tokens, inner_issues)
        statements = inner_parser.parse()
        return ast.DialectExpr(
            name="shell.command_substitution",
            args={"statements": statements},
            span=sp,
        )
    if kind == "arith":
        return ast.DialectExpr(
            name="shell.arithmetic",
            args={"expression": _arith(str(payload), line)},
            span=sp,
        )
    return ast.Literal(type="string", value=str(payload), span=sp)


def _arith(text: str, line: int) -> ast.Node:
    tokens = _ARITH_TOKEN_RE.findall(text)
    nodes: list[ast.Node] = []
    ops: list[str] = []
    for token in tokens:
        if token.isdigit():
            nodes.append(ast.Literal(type="number", value=token))
        elif re.match(r"[A-Za-z_]", token):
            nodes.append(ast.Variable(name=token))
        elif token in "+-*/%":
            ops.append(token)
    result: ast.Node = nodes[0] if nodes else ast.Literal(type="string", value=text)
    for index, op in enumerate(ops):
        right = nodes[index + 1] if index + 1 < len(nodes) else ast.Literal(type="null")
        result = ast.Binary(op=op, left=result, right=right, span=span(line))
    return result


def _as_condition(statements: list[ast.Node]) -> ast.Node:
    if len(statements) == 1:
        return statements[0]
    if not statements:
        return ast.Literal(type="boolean", value="true")
    return ast.DialectStatement(
        name="shell.list",
        args={"statements": statements},
        span=span(statements[0].span.start_line, statements[-1].span.end_line),
    )


def _set_background(node: ast.Node) -> None:
    if isinstance(node, ast.DialectStatement):
        node.args["background"] = ast.Literal(type="boolean", value="true")


def parse_script(text: str) -> tuple[list[ast.Node], dict[str, str], list[ast.Issue]]:
    tokens, issues = tokenize(text)
    parser = ShellParser(tokens, issues)
    statements = parser.parse()
    variables = collect_variables(statements)
    return statements, variables, parser.issues


def collect_variables(statements: list[ast.Node]) -> dict[str, str]:
    """Simple top-level constant assignments usable for path normalisation."""
    variables: dict[str, str] = {}
    for stmt in statements:
        nodes = [stmt]
        if isinstance(stmt, ast.DialectStatement) and stmt.name == "shell.and_or":
            continue
        for node in nodes:
            if isinstance(node, ast.DialectStatement) and node.name == "shell.command":
                for assign in node.args.get("assignments") or []:
                    if (
                        isinstance(assign, ast.Assign)
                        and isinstance(assign.target, ast.Variable)
                        and isinstance(assign.value, ast.Literal)
                        and assign.value.type == "string"
                    ):
                        variables[assign.target.name] = assign.value.value
    return variables


__all__ = ["ShellParser", "ShellScript", "collect_variables", "parse_script"]
