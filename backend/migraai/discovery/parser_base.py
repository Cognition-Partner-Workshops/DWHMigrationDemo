from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from migraai.discovery import ast
from migraai.discovery.lexer import Lexer, LexerConfig, Token, TokenKind


class ParseError(Exception):
    def __init__(self, message: str, token: Token) -> None:
        super().__init__(message)
        self.token = token


class ParseLimitError(ParseError):
    def __init__(
        self, message: str, token: Token, code: str = "parser.expression_depth_limit"
    ) -> None:
        super().__init__(message, token)
        self.code = code


class ParserBase:
    lexer_class: type[Lexer] = Lexer
    binary_ops: dict[str, tuple[int, int, str]] = {
        "OR": (10, 11, "or"),
        "AND": (20, 21, "and"),
        "=": (30, 31, "="),
        "!=": (30, 31, "!="),
        "<>": (30, 31, "<>"),
        "<": (30, 31, "<"),
        ">": (30, 31, ">"),
        "<=": (30, 31, "<="),
        ">=": (30, 31, ">="),
        "BETWEEN": (30, 31, "between"),
        "IN": (30, 31, "in"),
        "IS": (30, 31, "is"),
        "IS DISTINCT FROM": (30, 31, "is_distinct_from"),
        "IS NOT DISTINCT FROM": (30, 31, "is_not_distinct_from"),
        "LIKE": (30, 31, "like"),
        "ILIKE": (30, 31, "ilike"),
        "SIMILAR TO": (30, 31, "similar"),
        "RLIKE": (30, 31, "rlike"),
        "REGEXP": (30, 31, "regexp"),
        "||": (40, 41, "||"),
        "+": (50, 51, "+"),
        "-": (50, 51, "-"),
        "*": (60, 61, "*"),
        "/": (60, 61, "/"),
        "%": (60, 61, "%"),
        "^": (70, 70, "^"),
    }
    ansi_builtins = frozenset(
        {
            "ABS",
            "AVG",
            "ARRAY_AGG",
            "CARDINALITY",
            "CAST",
            "CEIL",
            "COALESCE",
            "CONCAT",
            "CORR",
            "COUNT",
            "COVAR_POP",
            "COVAR_SAMP",
            "CURRENT_DATE",
            "CURRENT_TIME",
            "CURRENT_TIMESTAMP",
            "CURRENT_USER",
            "DENSE_RANK",
            "FIRST_VALUE",
            "EXTRACT",
            "FLOOR",
            "LAG",
            "LAST_VALUE",
            "LEAD",
            "LENGTH",
            "LOWER",
            "MAX",
            "MIN",
            "MOD",
            "NULLIF",
            "OCTET_LENGTH",
            "PERCENT_RANK",
            "RANK",
            "REGR_AVGX",
            "REGR_AVGY",
            "REGR_COUNT",
            "REGR_INTERCEPT",
            "REGR_R2",
            "REGR_SLOPE",
            "REGR_SXX",
            "REGR_SXY",
            "REGR_SYY",
            "ROUND",
            "ROW_NUMBER",
            "SUBSTRING",
            "SUM",
            "TRIM",
            "UPPER",
            "CURRENT_ROLE",
            "SESSION_USER",
            "SYSTEM_USER",
            "USER",
        }
    )
    builtin_functions = ansi_builtins
    expression_depth_limit = 500
    file_time_limit_seconds = 30.0
    time_check_interval = 1000

    def __init__(
        self,
        tokens: str | list[Token] | None = None,
        config: LexerConfig | str | None = None,
        *,
        rel_path: str = "",
        lexer_config: LexerConfig | None = None,
        binary_ops: dict[str, tuple[int, int, str]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.text = tokens if isinstance(tokens, str) else ""
        self.rel_path = config if isinstance(config, str) else rel_path
        self.lexer_config = (
            lexer_config or (config if isinstance(config, LexerConfig) else None) or LexerConfig()
        )
        self._lexer: Lexer | None = None
        self.binary_ops = binary_ops if binary_ops is not None else type(self).binary_ops
        self.clock = clock if clock is not None else time.perf_counter
        self.tokens = list(tokens) if isinstance(tokens, list) else []
        self.index = 0
        self.issues: list[ast.Issue] = []
        self.result: ast.ParseResult | None = None
        self.default_schema: str | None = None
        self._started = 0.0
        self._expr_depth = 0
        self._next_time_check = self.time_check_interval

    def parse(self, text: str | None = None, rel_path: str | None = None) -> ast.ParseResult:
        if text is not None:
            self.text = text
        if rel_path is not None:
            self.rel_path = rel_path
        self.default_schema = None
        if text is not None or not self.tokens:
            lexer = self.lexer_class(self.text, self.lexer_config)
            self._lexer = lexer
            self.tokens = lexer.tokenize()
            self.issues = list(lexer.issues)
        else:
            self.issues = []
        self.index = 0
        self._started = self.clock()
        self._next_time_check = self.time_check_interval
        statements: list[ast.Node] = []
        while not self.at(TokenKind.EOF):
            if self.at(TokenKind.TERMINATOR) or self.at(TokenKind.DOT_COMMAND):
                token = self.advance()
                if token.kind is TokenKind.DOT_COMMAND:
                    statements.append(
                        ast.ClientCommand(text=token.text, span=self._token_span(token))
                    )
                continue
            start = self.index
            try:
                statement = self.parse_statement()
                statements.append(statement)
                self._update_default_schema(statement)
                self.accept(TokenKind.TERMINATOR)
            except ParseLimitError as error:
                self.issues.append(
                    ast.Issue("error", error.code, str(error), self._token_span(error.token))
                )
                end = len(self.tokens) - 1 if error.code == "parser.time_limit" else self.index
                statements.append(
                    ast.Unparsed(
                        text=self._slice_tokens(start, end),
                        reason=str(error),
                        span=self._token_span(self.tokens[start]),
                    )
                )
                if error.code == "parser.time_limit":
                    break
                self._recover(start)
            except ParseError as error:
                start_token = self.tokens[start]
                span = self._token_span(error.token)
                self.issues.append(ast.Issue("error", "parser.syntax", str(error), span))
                statements.append(
                    ast.Unparsed(
                        text=self._slice_tokens(start, self.index),
                        reason=str(error),
                        span=self._token_span(start_token),
                    )
                )
                self._recover(start)
            except RecursionError:
                token = self.peek()
                self.issues.append(
                    ast.Issue(
                        "error",
                        "parser.expression_depth_limit",
                        "Expression nesting exceeded the parser's safe recursion depth.",
                        self._token_span(token),
                    )
                )
                statements.append(
                    ast.Unparsed(
                        text=self._slice_tokens(start, self.index),
                        reason="Expression depth limit exceeded.",
                        span=self._token_span(self.tokens[start]),
                    )
                )
                self._recover(start)
            except Exception as error:
                token = self.peek()
                self.issues.append(
                    ast.Issue(
                        "error",
                        "parser.recovered",
                        f"Parser recovered from {type(error).__name__}.",
                        self._token_span(token),
                    )
                )
                statements.append(
                    ast.Unparsed(
                        text=self._slice_tokens(start, self.index + 1),
                        reason="Unexpected parser error.",
                        span=self._token_span(self.tokens[start]),
                    )
                )
                self._recover(start)
        self.result = ast.ParseResult(statements, self.issues, self.default_schema)
        return self.result

    def _update_default_schema(self, statement: ast.Node) -> None:
        if isinstance(statement, ast.Use) and statement.database.parts:
            self.default_schema = ".".join(part.value for part in statement.database.parts)
            return
        if not isinstance(statement, ast.SetStatement):
            return
        for item in statement.items:
            name = getattr(item, "name", None) or getattr(item, "target", None)
            if isinstance(name, ast.ColumnRef) and name.name.parts:
                name = name.name.parts[-1]
            if not isinstance(name, ast.Identifier) or name.value.casefold() not in {
                "schema",
                "database",
            }:
                continue
            value = getattr(item, "value", None)
            if isinstance(value, ast.ColumnRef) and value.name.parts:
                value = value.name
            if isinstance(value, ast.QualifiedName) and value.parts:
                self.default_schema = ".".join(part.value for part in value.parts)
            elif isinstance(value, ast.Identifier):
                self.default_schema = value.value
            elif isinstance(value, ast.Literal):
                self.default_schema = value.value

    def peek(self, offset: int = 0) -> Token:
        self._check_time()
        return self.tokens[min(self.index + offset, len(self.tokens) - 1)]

    def advance(self) -> Token:
        token = self.peek()
        if token.kind is not TokenKind.EOF:
            self.index += 1
        return token

    def at(self, kind: TokenKind | str, text: str | None = None) -> bool:
        token = self.peek()
        if isinstance(kind, TokenKind):
            return token.kind is kind and (text is None or token.text.casefold() == text.casefold())
        if kind in TokenKind.__members__:
            return token.kind.value == kind and (
                text is None or token.text.casefold() == text.casefold()
            )
        return token.text.casefold() == kind.casefold() and (
            text is None or token.text.casefold() == text.casefold()
        )

    def at_keyword(self, *words: str | int) -> bool:
        offset = words[-1] if words and isinstance(words[-1], int) else 0
        keywords = words[:-1] if words and isinstance(words[-1], int) else words
        token = self.peek(int(offset))
        return token.kind is TokenKind.IDENT and token.text.casefold() in {
            str(word).casefold() for word in keywords
        }

    def accept_keyword(self, *words: str) -> Token | None:
        return self.advance() if self.at_keyword(*words) else None

    def expect_keyword(self, *words: str) -> Token:
        token = self.accept_keyword(*words)
        if token is None:
            raise ParseError(f"Expected {' or '.join(words)}.", self.peek())
        return token

    def accept(self, kind: TokenKind | str, text: str | None = None) -> Token | None:
        token = self.peek()
        matched = token.kind is kind if isinstance(kind, TokenKind) else token.text == kind
        if matched and (text is None or token.text.casefold() == text.casefold()):
            return self.advance()
        return None

    def expect(self, kind: TokenKind | str, text: str | None = None) -> Token:
        token = self.accept(kind, text)
        if token is None:
            label = kind.value if isinstance(kind, TokenKind) else kind
            raise ParseError(f"Expected {label}.", self.peek())
        return token

    def parse_identifier(self) -> ast.Identifier:
        token = self.peek()
        if token.kind not in {TokenKind.IDENT, TokenKind.QUOTED_IDENT}:
            raise ParseError("Expected identifier.", token)
        self.advance()
        return ast.Identifier(
            value=token.value,
            quoted=token.kind is TokenKind.QUOTED_IDENT,
            span=self._token_span(token),
        )

    def parse_qualified_name(self) -> ast.QualifiedName:
        start = self.peek()
        parts = [self.parse_identifier()]
        while self.accept(TokenKind.PUNCT, "."):
            if self.accept(TokenKind.OPERATOR, "*"):
                parts.append(
                    ast.Identifier(value="*", span=self._token_span(self.tokens[self.index - 1]))
                )
                break
            parts.append(self.parse_identifier())
        return ast.QualifiedName(
            parts=parts, span=self._range_span(start, self.tokens[self.index - 1])
        )

    def parse_comma_list(self, parse_item: Callable[[], Any]) -> list[Any]:
        items = [parse_item()]
        while self.accept(TokenKind.PUNCT, ","):
            items.append(parse_item())
        return items

    def parse_statement(self) -> ast.Node:
        start = self.peek()
        node = self._parse_statement_impl()
        end = self.tokens[max(self.index - 1, 0)]
        node.span = ast.Span(
            start.line,
            start.col,
            end.line,
            end.col + len(end.text),
        )
        return node

    def _parse_statement_impl(self) -> ast.Node:
        extension = self.parse_statement_ext()
        if extension is not None:
            return extension
        if self.at_keyword("WITH") or self.at_keyword("SELECT") or self.at_keyword("VALUES"):
            return self.parse_query()
        if self.at_keyword("INSERT"):
            return self.parse_insert()
        if self.at_keyword("UPDATE"):
            return self.parse_update()
        if self.at_keyword("DELETE"):
            return self.parse_delete()
        if self.at_keyword("MERGE"):
            return self.parse_merge()
        if self.at_keyword("TRUNCATE"):
            return self.parse_truncate()
        if self.at_keyword("COPY"):
            return self.parse_copy()
        if self.at_keyword("LOAD"):
            return self.parse_load()
        if self.at_keyword("CREATE"):
            return self.parse_create()
        if self.at_keyword("ALTER"):
            return self.parse_alter()
        if self.at_keyword("DROP"):
            return self.parse_drop()
        if self.at_keyword("COMMENT"):
            return self.parse_comment()
        if self.at_keyword("RENAME"):
            return self.parse_rename()
        if self.at_keyword("GRANT"):
            return self.parse_grant()
        if self.at_keyword("REVOKE"):
            return self.parse_revoke()
        if self.at_keyword("BEGIN"):
            return self.parse_block()
        if self.at_keyword("SET"):
            return self.parse_set()
        if self.at_keyword("CALL"):
            return self.parse_call()
        if self.at_keyword("EXECUTE") or self.at_keyword("PREPARE"):
            return self.parse_dynamic_sql()
        if self.at_keyword("EXEC"):
            self.advance()
            return self.parse_call_after_keyword()
        if self.at_keyword("OPEN"):
            return self.parse_open_cursor()
        if self.at_keyword("FETCH"):
            return self.parse_fetch()
        if self.at_keyword("CLOSE"):
            return self.parse_close_cursor()
        if self.at_keyword("COMMIT"):
            self.advance()
            return ast.Commit()
        if self.at_keyword("ROLLBACK"):
            self.advance()
            savepoint = self.parse_identifier() if self.accept_keyword("TO") else None
            return ast.Rollback(savepoint=savepoint)
        if self.at_keyword("USE"):
            self.advance()
            return ast.Use(database=self.parse_qualified_name())
        if self.at_keyword("IF"):
            return self.parse_if()
        if self.at_keyword("WHILE"):
            return self.parse_while()
        if self.at_keyword("LOOP"):
            return self.parse_loop()
        if self.at_keyword("FOR"):
            return self.parse_for()
        if self.at_keyword("CASE"):
            return self.parse_case()
        if self.at_keyword("REPEAT"):
            return self.parse_repeat()
        if self.at_keyword("LEAVE"):
            self.advance()
            return ast.Leave(label=self.parse_identifier().value if self._is_name() else None)
        if self.at_keyword("EXIT"):
            self.advance()
            return ast.Exit(label=self.parse_identifier().value if self._is_name() else None)
        if self.at_keyword("CONTINUE"):
            self.advance()
            return ast.Continue(label=self.parse_identifier().value if self._is_name() else None)
        if self.at_keyword("RETURN"):
            self.advance()
            return ast.Return(expr=self.parse_expr() if not self._statement_end() else None)
        if self.at_keyword("RAISE"):
            self.advance()
            return ast.Raise(expr=self.parse_expr() if not self._statement_end() else None)
        if self.at_keyword("SIGNAL"):
            self.advance()
            condition = self.parse_identifier().value if self._is_name() else None
            return ast.Signal(condition=condition)
        if self.at_keyword("DECLARE"):
            return self.parse_declare()
        start = self.peek()
        expression = self.parse_expr()
        if self.accept(TokenKind.OPERATOR, ":=") or self.accept(TokenKind.OPERATOR, "="):
            value = self.parse_expr()
            return ast.Assign(target=expression, value=value, span=self._token_span(start))
        raise ParseError("Unsupported statement.", start)

    def parse_statement_ext(self) -> ast.Node | None:
        return None

    def parse_primary_ext(self) -> ast.Node | None:
        return None

    def parse_postfix_ext(self, left: ast.Node) -> ast.Node | None:
        return None

    def parse_expr(self, min_bp: int = 0) -> ast.Node:
        self._expr_depth += 1
        try:
            if self._expr_depth > self.expression_depth_limit:
                raise ParseLimitError("Expression depth limit exceeded.", self.peek())
            left = self._parse_prefix()
            while True:
                extension = self.parse_postfix_ext(left)
                if extension is not None:
                    left = extension
                    continue
                postfix = self._parse_postfix(left)
                if postfix is not None:
                    left = postfix
                    continue
                operator, negated = self._peek_infix()
                if operator is None:
                    break
                key = operator.casefold()
                if (
                    key not in {item.casefold() for item in self.binary_ops}
                    and key.upper() not in self.binary_ops
                ):
                    break
                table_key = next(item for item in self.binary_ops if item.casefold() == key)
                lbp, rbp, op_name = self.binary_ops[table_key]
                if lbp < min_bp:
                    break
                self._consume_infix(operator, negated)
                if operator.upper() == "IS":
                    is_not = negated or bool(self.accept_keyword("NOT"))
                    if self.accept_keyword("NULL"):
                        left = ast.IsNull(expr=left, negated=is_not)
                        continue
                    right = self.parse_expr(rbp)
                    left = ast.Binary(op="is_not" if is_not else "is", left=left, right=right)
                    continue
                if operator.upper() in {"=", "!=", "<>", "<", ">", "<=", ">="} and (
                    self.at_keyword("ANY") or self.at_keyword("ALL") or self.at_keyword("SOME")
                ):
                    quantifier = self.advance().text.casefold()
                    self.expect(TokenKind.PUNCT, "(")
                    query = self.parse_query()
                    self.expect(TokenKind.PUNCT, ")")
                    quantified = ast.Quantified(
                        op=operator.casefold(), quantifier=quantifier, query=query
                    )
                    left = ast.Binary(op=operator.casefold(), left=left, right=quantified)
                    continue
                if operator.upper() in {"IN", "LIKE", "ILIKE", "SIMILAR TO", "RLIKE", "REGEXP"}:
                    if operator.upper() == "IN":
                        self.expect(TokenKind.PUNCT, "(")
                        if (
                            self.at_keyword("SELECT")
                            or self.at_keyword("WITH")
                            or self.at_keyword("VALUES")
                        ):
                            query = self.parse_query()
                            self.expect(TokenKind.PUNCT, ")")
                            left = ast.InSubquery(expr=left, query=query, negated=negated)
                        else:
                            values = (
                                self.parse_comma_list(self.parse_expr)
                                if not self.at(TokenKind.PUNCT, ")")
                                else []
                            )
                            self.expect(TokenKind.PUNCT, ")")
                            left = ast.InList(expr=left, items=values, negated=negated)
                    else:
                        quantifier = None
                        if self.accept_keyword("ANY") or self.accept_keyword("ALL"):
                            quantifier = self.tokens[self.index - 1].text.casefold()
                        parenthesized = bool(self.accept(TokenKind.PUNCT, "("))
                        if parenthesized and (
                            self.at_keyword("SELECT")
                            or self.at_keyword("WITH")
                            or self.at_keyword("VALUES")
                        ):
                            pattern = ast.Subquery(self.parse_query())
                        elif quantifier is not None:
                            values = (
                                self.parse_comma_list(self.parse_expr)
                                if parenthesized
                                else [self.parse_expr(rbp)]
                            )
                            pattern = ast.ArrayLiteral(values)
                        else:
                            pattern = self.parse_expr()
                        if parenthesized:
                            self.expect(TokenKind.PUNCT, ")")
                        escape = self.parse_expr(rbp) if self.accept_keyword("ESCAPE") else None
                        left = ast.Like(
                            expr=left,
                            pattern=pattern,
                            escape=escape,
                            op="similar"
                            if operator.upper() == "SIMILAR TO"
                            else operator.casefold(),
                            negated=negated,
                            quantifier=quantifier,
                        )
                    continue
                if operator.upper() == "BETWEEN":
                    low = self.parse_expr(rbp)
                    self.expect_keyword("AND")
                    high = self.parse_expr(rbp)
                    left = ast.Between(expr=left, low=low, high=high, negated=negated)
                    continue
                right = self.parse_expr(rbp)
                left = ast.Binary(op=op_name, left=left, right=right)
            return left
        finally:
            self._expr_depth -= 1

    def _parse_prefix(self) -> ast.Node:
        extension = self.parse_primary_ext()
        if extension is not None:
            return extension
        token = self.peek()
        if token.kind in {
            TokenKind.STRING,
            TokenKind.NATIONAL_STRING,
            TokenKind.BIT_STRING,
            TokenKind.HEX_STRING,
        }:
            self.advance()
            literal_type = {
                TokenKind.STRING: "string",
                TokenKind.NATIONAL_STRING: "national_string",
                TokenKind.BIT_STRING: "bit",
                TokenKind.HEX_STRING: "hex",
            }[token.kind]
            return ast.Literal(literal_type, token.value, span=self._token_span(token))
        if token.kind is TokenKind.NUMBER:
            self.advance()
            return ast.Literal("number", token.value, span=self._token_span(token))
        if token.kind is TokenKind.PARAM:
            self.advance()
            return ast.Param(token.text, span=self._token_span(token))
        if token.kind in {TokenKind.IDENT, TokenKind.QUOTED_IDENT}:
            if token.kind is TokenKind.IDENT and token.text.upper() in {
                "AND",
                "AS",
                "BY",
                "ELSE",
                "END",
                "EXCEPT",
                "FETCH",
                "FROM",
                "GROUP",
                "HAVING",
                "INTERSECT",
                "JOIN",
                "LIMIT",
                "MINUS",
                "OFFSET",
                "ON",
                "OR",
                "ORDER",
                "QUALIFY",
                "ROWS",
                "SET",
                "THEN",
                "UNION",
                "USING",
                "VALUES",
                "WHEN",
                "WHERE",
            }:
                raise ParseError("Expected expression.", token)
            if (
                token.kind is TokenKind.IDENT
                and token.text.upper()
                in {
                    "DATE",
                    "TIME",
                    "TIMESTAMP",
                }
                and self.peek(1).kind is TokenKind.STRING
            ):
                self.advance()
                value = self.advance()
                return ast.Literal(
                    token.text.casefold(), value.value, span=self._range_span(token, value)
                )
            if self.at_keyword("NULL"):
                self.advance()
                return ast.Literal("null", "null", span=self._token_span(token))
            if self.at_keyword("TRUE") or self.at_keyword("FALSE"):
                self.advance()
                return ast.Literal("boolean", token.text.casefold(), span=self._token_span(token))
            if self.at_keyword("CASE"):
                return self.parse_case()
            if self.at_keyword("EXISTS"):
                self.advance()
                self.expect(TokenKind.PUNCT, "(")
                query = self.parse_query()
                self.expect(TokenKind.PUNCT, ")")
                return ast.Exists(query=query, span=self._token_span(token))
            if self.at_keyword("NOT"):
                self.advance()
                return ast.Unary("not", self.parse_expr(25), span=self._token_span(token))
            if self.at_keyword("PRIOR"):
                self.advance()
                return ast.Unary("prior", self.parse_expr(80), span=self._token_span(token))
            if any(self.at_keyword(kind) for kind in ("CAST", "TRY_CAST", "SAFE_CAST")):
                self.advance()
                self.expect(TokenKind.PUNCT, "(")
                value = self.parse_expr()
                self.expect_keyword("AS")
                datatype = self.parse_data_type()
                self.expect(TokenKind.PUNCT, ")")
                return ast.Cast(
                    value, datatype, token.text.casefold(), span=self._token_span(token)
                )
            if self.at_keyword("CONVERT") or self.at_keyword("TRY_CONVERT"):
                self.advance()
                self.expect(TokenKind.PUNCT, "(")
                datatype = self.parse_data_type()
                self.expect(TokenKind.PUNCT, ",")
                value = self.parse_expr()
                format_expr = self.parse_expr() if self.accept(TokenKind.PUNCT, ",") else None
                self.expect(TokenKind.PUNCT, ")")
                return ast.Cast(
                    value,
                    datatype,
                    "convert",
                    format_expr,
                    span=self._token_span(token),
                )
            if self.at_keyword("EXTRACT"):
                self.advance()
                self.expect(TokenKind.PUNCT, "(")
                field = self.advance().text
                self.expect_keyword("FROM")
                value = self.parse_expr()
                self.expect(TokenKind.PUNCT, ")")
                return ast.Extract(field, value, span=self._token_span(token))
            if self.at_keyword("INTERVAL"):
                self.advance()
                value = self.parse_expr(80)
                unit = self.advance().text if self._is_name() else ""
                to_unit = self.advance().text if self.accept_keyword("TO") else None
                return ast.Interval(value, unit, to_unit, span=self._token_span(token))
            if self.at_keyword("ARRAY") and self.peek(1).text == "[":
                self.advance()
                self.advance()
                items = (
                    self.parse_comma_list(self.parse_expr)
                    if not self.at(TokenKind.PUNCT, "]")
                    else []
                )
                self.expect(TokenKind.PUNCT, "]")
                return ast.ArrayLiteral(items, span=self._token_span(token))
            if self.at_keyword("STRUCT") and self.peek(1).text == "(":
                self.advance()
                self.advance()
                fields: list[ast.StructField] = []
                if not self.at(TokenKind.PUNCT, ")"):
                    for index, value in enumerate(self.parse_comma_list(self.parse_expr)):
                        fields.append(
                            ast.StructField(
                                name=ast.Identifier(value=str(index)),
                                value=value,
                            )
                        )
                self.expect(TokenKind.PUNCT, ")")
                return ast.StructLiteral(fields, span=self._token_span(token))
            name = self.parse_qualified_name()
            if name.parts and name.parts[-1].value == "*":
                return ast.Star(
                    qualifier=ast.QualifiedName(parts=name.parts[:-1]),
                    span=self._range_span(token, self.tokens[self.index - 1]),
                )
            return ast.ColumnRef(name, span=self._range_span(token, self.tokens[self.index - 1]))
        if token.kind is TokenKind.QUOTED_IDENT:
            self.advance()
            identifier = ast.Identifier(token.value, True, span=self._token_span(token))
            return ast.ColumnRef(ast.QualifiedName([identifier], span=self._token_span(token)))
        if token.kind is TokenKind.OPERATOR and token.text in {"+", "-", "~"}:
            self.advance()
            return ast.Unary(token.text, self.parse_expr(80), span=self._token_span(token))
        if token.kind is TokenKind.PUNCT and token.text == "(":
            self.advance()
            if self.at_keyword("SELECT") or self.at_keyword("WITH") or self.at_keyword("VALUES"):
                query = self.parse_query()
                self.expect(TokenKind.PUNCT, ")")
                return ast.Subquery(query, span=self._token_span(token))
            value = self.parse_expr()
            self.expect(TokenKind.PUNCT, ")")
            return value
        if token.kind is TokenKind.OPERATOR and token.text == "*":
            self.advance()
            return ast.Star(span=self._token_span(token))
        raise ParseError("Expected expression.", token)

    def _parse_postfix(self, left: ast.Node) -> ast.Node | None:
        if isinstance(left, ast.Star) and self.accept_keyword("EXCEPT"):
            self.expect(TokenKind.PUNCT, "(")
            left.except_ = self._parse_identifier_list()
            return left
        if isinstance(left, ast.Star) and self.accept_keyword("REPLACE"):
            self.expect(TokenKind.PUNCT, "(")
            aliases = []
            while True:
                value = self.parse_expr()
                self.expect_keyword("AS")
                aliases.append(ast.Alias(name=self.parse_identifier(), expr=value))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
            left.replace = aliases
            return left
        if self.accept(TokenKind.PUNCT, "("):
            if not isinstance(left, ast.ColumnRef):
                raise ParseError("Only named functions can be called.", self.peek())
            name = left.name
            distinct = bool(self.accept_keyword("DISTINCT"))
            star = bool(self.accept(TokenKind.OPERATOR, "*"))
            args = (
                []
                if star or self.at(TokenKind.PUNCT, ")")
                else self.parse_comma_list(self._parse_function_arg)
            )
            self.expect(TokenKind.PUNCT, ")")
            call = ast.FunctionCall(
                name=name, args=args, distinct=distinct, star=star, span=left.span
            )
            if self.accept_keyword("WITHIN"):
                self.expect_keyword("GROUP")
                self.expect(TokenKind.PUNCT, "(")
                self.expect_keyword("ORDER")
                call.within_group = self._parse_order_by()
                self.expect(TokenKind.PUNCT, ")")
            if self.accept_keyword("FILTER"):
                self.expect(TokenKind.PUNCT, "(")
                self.expect_keyword("WHERE")
                call.filter = self.parse_expr()
                self.expect(TokenKind.PUNCT, ")")
            if self.accept_keyword("OVER"):
                if self.at(TokenKind.PUNCT, "("):
                    call.over = self.parse_window_spec()
                else:
                    call.over = ast.WindowSpec(name=self.parse_identifier())
            if self.accept_keyword("IGNORE"):
                self.expect_keyword("NULLS")
                call.ignore_nulls = True
            elif self.accept_keyword("RESPECT"):
                self.expect_keyword("NULLS")
                call.ignore_nulls = False
            return call
        if self.accept(TokenKind.PUNCT, "["):
            index = self.parse_expr()
            self.expect(TokenKind.PUNCT, "]")
            return ast.Subscript(left, index, span=getattr(left, "span", ast.Span()))
        if self.accept(TokenKind.PUNCT, "."):
            field = self.parse_identifier()
            if isinstance(left, ast.ColumnRef):
                left.name.parts.append(field)
                return left
            return ast.FieldAccess(left, field, span=getattr(left, "span", ast.Span()))
        if self.accept(TokenKind.OPERATOR, "::"):
            return ast.Cast(
                left,
                self.parse_data_type(),
                "double_colon",
                span=getattr(left, "span", ast.Span()),
            )
        if self.accept_keyword("COLLATE"):
            return ast.Collate(
                left, self.parse_qualified_name(), span=getattr(left, "span", ast.Span())
            )
        return None

    def _peek_infix(self) -> tuple[str | None, bool]:
        token = self.peek()
        word = token.text.upper() if token.kind is TokenKind.IDENT else token.text
        if word == "NOT" and (
            self.at_keyword("BETWEEN", 1)
            or self.at_keyword("IN", 1)
            or self.at_keyword("LIKE", 1)
            or self.at_keyword("ILIKE", 1)
            or self.at_keyword("SIMILAR", 1)
            or self.at_keyword("RLIKE", 1)
            or self.at_keyword("REGEXP", 1)
        ):
            if self.at_keyword("SIMILAR", 1):
                return "SIMILAR TO", True
            return self.peek(1).text.upper(), True
        if word == "SIMILAR" and self.at_keyword("TO", 1):
            return "SIMILAR TO", False
        if word == "IS" and self.at_keyword("DISTINCT", 1):
            return "IS DISTINCT FROM", False
        if word == "IS" and self.at_keyword("NOT", 1) and self.at_keyword("DISTINCT", 2):
            return "IS NOT DISTINCT FROM", False
        if word in {"BETWEEN", "IN", "LIKE", "ILIKE", "IS"}:
            return word, False
        if token.kind in {TokenKind.OPERATOR, TokenKind.IDENT}:
            return word, False
        return None, False

    def _consume_infix(self, operator: str, negated: bool) -> None:
        if negated:
            self.advance()
        if operator in {"IS DISTINCT FROM", "IS NOT DISTINCT FROM"}:
            self.advance()
            self.advance()
            self.advance()
            if operator == "IS NOT DISTINCT FROM":
                self.advance()
        elif operator == "SIMILAR TO":
            self.advance()
            self.advance()
        else:
            self.advance()

    def parse_case(self) -> ast.Case:
        start = self.expect_keyword("CASE")
        operand = None if self.at_keyword("WHEN") else self.parse_expr()
        whens: list[ast.When] = []
        while self.accept_keyword("WHEN"):
            condition = self.parse_expr()
            self.expect_keyword("THEN")
            whens.append(ast.When(condition, self.parse_expr()))
        else_expr = self.parse_expr() if self.accept_keyword("ELSE") else None
        self.expect_keyword("END")
        return ast.Case(operand, whens, else_expr, span=self._token_span(start))

    def _parse_function_arg(self) -> ast.Node:
        if self._is_name() and self.peek(1).text == "=>":
            name = self.parse_identifier()
            self.advance()
            return ast.NamedArg(name, self.parse_expr())
        return self.parse_expr()

    def parse_data_type(self) -> ast.DataType:
        start = self.peek()
        name = self.parse_qualified_name()
        params: list[ast.Node | str] = []
        type_word = name.parts[-1].value.casefold() if name.parts else ""
        if type_word in {"double", "character", "national", "timestamp", "time"}:
            extra = []
            if self.at_keyword("PRECISION", "VARYING"):
                extra.append(self.advance().text.casefold())
            elif self.accept_keyword("WITH"):
                extra.append("with")
                if self.at_keyword("TIME", "LOCAL"):
                    extra.append(self.advance().text.casefold())
                if self.at_keyword("TIME") and extra[-1] == "local":
                    extra.append(self.advance().text.casefold())
                elif self.accept_keyword("TIME"):
                    extra.append("time")
                if self.accept_keyword("ZONE"):
                    extra.append("zone")
            elif self.accept_keyword("WITHOUT"):
                extra.append("without")
                if self.accept_keyword("TIME"):
                    extra.extend(["time", "zone"] if self.accept_keyword("ZONE") else ["time"])
            for word in extra:
                name.parts.append(ast.Identifier(value=word))
        children: list[ast.DataType | ast.StructField] = []
        if self.accept(TokenKind.OPERATOR, "<"):
            if type_word == "struct":
                while not self.at(TokenKind.OPERATOR, ">") and not self.at(TokenKind.EOF):
                    field_name = self.parse_identifier()
                    field_type = self.parse_data_type()
                    children.append(ast.StructField(name=field_name, type=field_type))
                    if not self.accept(TokenKind.PUNCT, ","):
                        break
            else:
                while not self.at(TokenKind.OPERATOR, ">") and not self.at(TokenKind.EOF):
                    children.append(self.parse_data_type())
                    if not self.accept(TokenKind.PUNCT, ","):
                        break
            self.expect(TokenKind.OPERATOR, ">")
        if self.accept(TokenKind.PUNCT, "("):
            if not self.at(TokenKind.PUNCT, ")"):
                params = self.parse_comma_list(lambda: self.parse_expr(80))
            self.expect(TokenKind.PUNCT, ")")
        dims: list[int | None] = []
        while self.accept(TokenKind.PUNCT, "["):
            dims.append(int(self.advance().text) if self.at(TokenKind.NUMBER) else None)
            self.expect(TokenKind.PUNCT, "]")
        return ast.DataType(
            name,
            params,
            children,
            dims,
            span=self._range_span(start, self.tokens[self.index - 1]),
        )

    def parse_query(self) -> ast.Query:
        start = self.peek()
        with_clause = None
        if self.accept_keyword("WITH"):
            recursive = bool(self.accept_keyword("RECURSIVE"))
            ctes = []
            while True:
                cte_start = self.peek()
                name = self.parse_identifier()
                columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
                self.expect_keyword("AS")
                materialized = None
                if self.accept_keyword("NOT"):
                    self.expect_keyword("MATERIALIZED")
                    materialized = False
                elif self.accept_keyword("MATERIALIZED"):
                    materialized = True
                self.expect(TokenKind.PUNCT, "(")
                query = self.parse_query()
                self.expect(TokenKind.PUNCT, ")")
                ctes.append(
                    ast.Cte(name, columns, query, materialized, span=self._token_span(cte_start))
                )
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            with_clause = ast.With(recursive, ctes, span=self._token_span(start))
        body_stack: list[ast.Node] = [self._parse_query_body()]
        operator_stack: list[tuple[str, bool]] = []
        precedence = {"union": 1, "except": 1, "minus": 1, "intersect": 2}
        while self.at_keyword("UNION", "INTERSECT", "EXCEPT", "MINUS"):
            op = self.advance().text.casefold()
            all_rows = bool(self.accept_keyword("ALL"))
            if not all_rows:
                self.accept_keyword("DISTINCT")
            while operator_stack and precedence[operator_stack[-1][0]] >= precedence[op]:
                previous_op, previous_all = operator_stack.pop()
                right = body_stack.pop()
                left = body_stack.pop()
                body_stack.append(
                    ast.SetOperation(
                        previous_op, previous_all, self._as_query(left), self._as_query(right)
                    )
                )
            operator_stack.append((op, all_rows))
            body_stack.append(self._parse_query_body())
        while operator_stack:
            op, all_rows = operator_stack.pop()
            right = body_stack.pop()
            left = body_stack.pop()
            body_stack.append(
                ast.SetOperation(op, all_rows, self._as_query(left), self._as_query(right))
            )
        body = body_stack[0]
        order_by = self._parse_order_by() if self.accept_keyword("ORDER") else []
        if order_by and self.tokens[self.index - len(order_by) - 1].text.upper() == "ORDER":
            pass
        limit = self._parse_limit() if self._is_limit_start() else None
        return ast.Query(with_clause, body, order_by, limit, span=self._token_span(start))

    def _parse_query_body(self) -> ast.Select | ast.SetOperation | ast.Values | ast.Subquery:
        if self.accept(TokenKind.PUNCT, "("):
            query = self.parse_query()
            self.expect(TokenKind.PUNCT, ")")
            return ast.Subquery(query)
        if self.at_keyword("SELECT"):
            return self.parse_select()
        if self.at_keyword("VALUES"):
            return self.parse_values()
        raise ParseError("Expected query.", self.peek())

    def _as_query(self, body: ast.Node) -> ast.Query:
        return body if isinstance(body, ast.Query) else ast.Query(body=body)

    def parse_values(self) -> ast.Values:
        start = self.expect_keyword("VALUES")
        rows: list[ast.ValuesRow] = []
        while True:
            self.expect(TokenKind.PUNCT, "(")
            values = (
                self.parse_comma_list(self.parse_expr) if not self.at(TokenKind.PUNCT, ")") else []
            )
            self.expect(TokenKind.PUNCT, ")")
            rows.append(ast.ValuesRow(values))
            if not self.accept(TokenKind.PUNCT, ","):
                break
        return ast.Values(rows, span=self._token_span(start))

    def parse_select(self) -> ast.Select:
        start = self.expect_keyword("SELECT")
        distinct = bool(self.accept_keyword("DISTINCT"))
        distinct_on: list[ast.Node] = []
        if distinct and self.accept_keyword("ON"):
            self.expect(TokenKind.PUNCT, "(")
            distinct_on = self.parse_comma_list(self.parse_expr)
            self.expect(TokenKind.PUNCT, ")")
        if not distinct:
            self.accept_keyword("ALL")
        top = None
        if self.accept_keyword("TOP"):
            self.accept(TokenKind.PUNCT, "(")
            count = self.parse_expr()
            self.accept(TokenKind.PUNCT, ")")
            percent = bool(self.accept_keyword("PERCENT"))
            ties = False
            if self.accept_keyword("WITH"):
                self.expect_keyword("TIES")
                ties = True
            top = ast.Top(count, percent, ties)
        projections = self.parse_comma_list(self._parse_select_item)
        into = self.parse_qualified_name() if self.accept_keyword("INTO") else None
        from_items: list[ast.Node] = []
        if self.accept_keyword("FROM"):
            from_items = self._parse_from_list()
        where = self.parse_expr() if self.accept_keyword("WHERE") else None
        group_by = self._parse_group_by() if self.accept_keyword("GROUP") else None
        having = self.parse_expr() if self.accept_keyword("HAVING") else None
        windows: dict[str, ast.WindowSpec] = {}
        if self.accept_keyword("WINDOW"):
            while True:
                name = self.parse_identifier()
                self.expect_keyword("AS")
                windows[name.value] = self.parse_window_spec()
                if not self.accept(TokenKind.PUNCT, ","):
                    break
        qualify = self.parse_expr() if self.accept_keyword("QUALIFY") else None
        return ast.Select(
            distinct=distinct,
            distinct_on=distinct_on,
            top=top,
            projections=projections,
            into=into,
            from_=from_items,
            where=where,
            group_by=group_by,
            having=having,
            qualify=qualify,
            windows=windows,
            span=self._token_span(start),
        )

    def _parse_select_item(self) -> ast.SelectItem:
        start = self.peek()
        expr = self.parse_expr()
        alias = None
        if self.accept_keyword("AS"):
            alias = self.parse_identifier()
        elif self._can_be_alias():
            alias = self.parse_identifier()
        return ast.SelectItem(expr, alias, span=self._token_span(start))

    def _parse_from_list(self) -> list[ast.Node]:
        items = [self._parse_join()]
        while self.accept(TokenKind.PUNCT, ","):
            items.append(self._parse_join())
        return items

    def _parse_join(self) -> ast.Node:
        left = self._parse_from_item()
        while True:
            natural = bool(self.accept_keyword("NATURAL"))
            join_kind = "inner"
            if self.at_keyword("SEMI") or self.at_keyword("ANTI"):
                join_kind = self.advance().text.casefold()
            elif self.at_keyword("LEFT") or self.at_keyword("RIGHT") or self.at_keyword("FULL"):
                join_kind = self.advance().text.casefold()
                if self.at_keyword("SEMI") or self.at_keyword("ANTI"):
                    join_kind = f"{join_kind}_{self.advance().text.casefold()}"
                self.accept_keyword("OUTER")
            elif self.at_keyword("CROSS"):
                join_kind = self.advance().text.casefold()
            elif self.at_keyword("INNER"):
                self.advance()
                join_kind = "inner"
            if not self.accept_keyword("JOIN"):
                if natural:
                    raise ParseError("Expected JOIN after NATURAL.", self.peek())
                break
            right = self._parse_from_item()
            condition = None
            using: list[ast.Identifier] = []
            if self.accept_keyword("ON"):
                condition = self.parse_expr()
            elif self.accept_keyword("USING"):
                self.expect(TokenKind.PUNCT, "(")
                using = self._parse_identifier_list()
            left = ast.Join(
                join_type=f"{'natural_' if natural else ''}{join_kind}",
                left=left,
                right=right,
                on=condition,
                using=using,
            )
        return left

    def _parse_from_item(self) -> ast.Node:
        lateral = bool(self.accept_keyword("LATERAL"))
        start = self.peek()
        if self.accept(TokenKind.PUNCT, "("):
            if self.at_keyword("SELECT") or self.at_keyword("WITH") or self.at_keyword("VALUES"):
                query = self.parse_query()
                self.expect(TokenKind.PUNCT, ")")
                alias = self._parse_optional_alias()
                return ast.SubqueryRef(query, alias, lateral, span=self._token_span(start))
            nested = self._parse_join()
            self.expect(TokenKind.PUNCT, ")")
            return nested
        if self.at_keyword("VALUES"):
            values = self.parse_values()
            alias = self._parse_optional_alias()
            columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
            return ast.ValuesRef(values, alias, columns, span=self._token_span(start))
        name = self.parse_qualified_name()
        if self.at(TokenKind.PUNCT, "("):
            left = ast.ColumnRef(name)
            call = self._parse_postfix(left)
            alias = self._parse_optional_alias()
            return ast.TableFunctionRef(call, alias, lateral, span=self._token_span(start))
        alias = self._parse_optional_alias()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        temporal = None
        if self.accept_keyword("FOR"):
            temporal_args: dict[str, ast.Node | str | None] = {}
            if self.accept_keyword("SYSTEM_TIME"):
                temporal_args["kind"] = "system_time"
            if self.accept_keyword("AS"):
                if self.accept_keyword("OF"):
                    temporal_args["mode"] = "as_of"
                    temporal_args["operator"] = "of"
                    temporal_args["value"] = self.parse_expr()
                else:
                    temporal_args["mode"] = self.advance().text.casefold()
            if self.at_keyword("OF", "FROM", "BETWEEN"):
                temporal_args["operator"] = self.advance().text.casefold()
                temporal_args["value"] = self.parse_expr()
                if temporal_args["operator"] == "between":
                    self.expect_keyword("AND")
                    temporal_args["end"] = self.parse_expr()
            temporal = ast.DialectExpr(name="temporal", args=temporal_args)
        sample = None
        if self.accept_keyword("TABLESAMPLE") or self.accept_keyword("SAMPLE"):
            if self._is_name():
                self.advance()
            if self.accept(TokenKind.PUNCT, "("):
                sample = self.parse_expr()
                self.expect(TokenKind.PUNCT, ")")
            else:
                sample = self.parse_expr(80)
        hints: list[str] = []
        if self.accept_keyword("WITH") and self.accept(TokenKind.PUNCT, "("):
            hints = [self.advance().text]
            while self.accept(TokenKind.PUNCT, ","):
                hints.append(self.advance().text)
            self.expect(TokenKind.PUNCT, ")")
        return ast.TableRef(
            name,
            alias,
            columns,
            temporal,
            sample,
            hints,
            span=self._token_span(start),
        )

    def _parse_optional_alias(self) -> ast.Identifier | None:
        self.accept_keyword("AS")
        return self.parse_identifier() if self._can_be_alias() else None

    def _parse_group_by(self) -> ast.GroupBy:
        self.expect_keyword("BY")
        all_items = bool(self.accept_keyword("ALL"))
        items: list[ast.Node] = []
        rollups: list[ast.Rollup] = []
        cubes: list[ast.Cube] = []
        grouping_sets: list[ast.GroupingSets] = []
        while True:
            if self.accept_keyword("ROLLUP"):
                self.expect(TokenKind.PUNCT, "(")
                rollups.append(ast.Rollup(self.parse_comma_list(self.parse_expr)))
                self.expect(TokenKind.PUNCT, ")")
            elif self.accept_keyword("CUBE"):
                self.expect(TokenKind.PUNCT, "(")
                cubes.append(ast.Cube(self.parse_comma_list(self.parse_expr)))
                self.expect(TokenKind.PUNCT, ")")
            elif self.accept_keyword("GROUPING"):
                self.expect_keyword("SETS")
                self.expect(TokenKind.PUNCT, "(")
                sets: list[list[ast.Node]] = []
                while True:
                    self.expect(TokenKind.PUNCT, "(")
                    sets.append(
                        self.parse_comma_list(self.parse_expr)
                        if not self.at(TokenKind.PUNCT, ")")
                        else []
                    )
                    self.expect(TokenKind.PUNCT, ")")
                    if not self.accept(TokenKind.PUNCT, ","):
                        break
                grouping_sets.append(ast.GroupingSets(sets))
                self.expect(TokenKind.PUNCT, ")")
            else:
                items.append(self.parse_expr())
            if not self.accept(TokenKind.PUNCT, ","):
                break
        return ast.GroupBy(items, rollups, cubes, grouping_sets, all_items)

    def _parse_order_by(self) -> list[ast.OrderItem]:
        self.expect_keyword("BY")
        items: list[ast.OrderItem] = []
        while True:
            expr = self.parse_expr()
            desc = bool(self.accept_keyword("DESC"))
            if not desc:
                self.accept_keyword("ASC")
            nulls = None
            if self.accept_keyword("NULLS"):
                nulls = "first" if self.accept_keyword("FIRST") else "last"
                if nulls == "last":
                    self.expect_keyword("LAST")
            items.append(ast.OrderItem(expr, desc, nulls))
            if not self.accept(TokenKind.PUNCT, ","):
                break
        return items

    def parse_window_spec(self) -> ast.WindowSpec:
        self.expect(TokenKind.PUNCT, "(")
        name = (
            self.parse_identifier()
            if self._is_name()
            and not self.at_keyword("PARTITION", "ORDER", "ROWS", "RANGE", "GROUPS")
            else None
        )
        partition_by = []
        order_by = []
        frame = None
        if self.accept_keyword("PARTITION"):
            self.expect_keyword("BY")
            partition_by = self.parse_comma_list(self.parse_expr)
        if self.accept_keyword("ORDER"):
            order_by = self._parse_order_by()
        if self.at_keyword("ROWS") or self.at_keyword("RANGE") or self.at_keyword("GROUPS"):
            unit = self.advance().text.casefold()
            if self.accept_keyword("BETWEEN"):
                start = self._parse_frame_bound()
                self.expect_keyword("AND")
                end = self._parse_frame_bound()
            else:
                start, end = self._parse_frame_bound(), None
            exclude = None
            if self.accept_keyword("EXCLUDE"):
                if self.accept_keyword("NO"):
                    self.expect_keyword("OTHERS")
                    exclude = "no_others"
                elif self.accept_keyword("CURRENT"):
                    self.expect_keyword("ROW")
                    exclude = "current_row"
                else:
                    exclude = self.advance().text.casefold()
            frame = ast.Frame(unit, start, end, exclude)
        self.expect(TokenKind.PUNCT, ")")
        return ast.WindowSpec(name, partition_by, order_by, frame)

    def _parse_frame_bound(self) -> ast.FrameBound:
        if self.accept_keyword("UNBOUNDED"):
            direction = self.advance().text.casefold()
            return ast.FrameBound(f"unbounded_{direction}")
        if self.accept_keyword("CURRENT"):
            self.expect_keyword("ROW")
            return ast.FrameBound("current_row")
        value = self.parse_expr(80)
        direction = self.advance().text.casefold()
        return ast.FrameBound(direction, value)

    def _is_limit_start(self) -> bool:
        return any(self.at_keyword(word) for word in ("LIMIT", "OFFSET", "FETCH"))

    def _parse_limit(self) -> ast.Limit:
        count = None
        offset = None
        fetch = False
        ties = False
        percent = False
        if self.accept_keyword("LIMIT"):
            first = self.parse_expr(80)
            if self.accept(TokenKind.PUNCT, ","):
                offset, count = first, self.parse_expr(80)
            else:
                count = first
        if self.accept_keyword("OFFSET"):
            offset = self.parse_expr(80)
            self.accept_keyword("ROW")
            self.accept_keyword("ROWS")
        if self.accept_keyword("FETCH"):
            fetch = True
            self.accept_keyword("FIRST") or self.accept_keyword("NEXT")
            count = (
                self.parse_expr(80)
                if not self.at_keyword("ROW") and not self.at_keyword("ROWS")
                else None
            )
            percent = bool(self.accept_keyword("PERCENT"))
            self.accept_keyword("ROW") or self.accept_keyword("ROWS")
            self.accept_keyword("ONLY")
            if self.accept_keyword("WITH"):
                self.expect_keyword("TIES")
                ties = True
        return ast.Limit(count, offset, fetch, ties, percent)

    def parse_insert(self) -> ast.Insert:
        start = self.expect_keyword("INSERT")
        overwrite = bool(self.accept_keyword("OVERWRITE"))
        self.accept_keyword("INTO") or self.accept_keyword("TABLE")
        target = self.parse_qualified_name()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        partition = []
        if self.accept_keyword("PARTITION"):
            if self.accept(TokenKind.PUNCT, "("):
                partition = self.parse_comma_list(self._parse_assignment)
                self.expect(TokenKind.PUNCT, ")")
        if self.accept_keyword("DEFAULT"):
            self.expect_keyword("VALUES")
            source: ast.Query | ast.Values | ast.DefaultValues = ast.DefaultValues()
        elif self.at_keyword("VALUES"):
            source = self.parse_values()
        else:
            source = self.parse_query()
        return ast.Insert(
            target,
            columns,
            source,
            overwrite,
            partition,
            span=self._token_span(start),
        )

    def parse_update(self) -> ast.Update:
        start = self.expect_keyword("UPDATE")
        target = self.parse_qualified_name()
        alias = self._parse_optional_alias()
        self.expect_keyword("SET")
        assignments = self.parse_comma_list(self._parse_assignment)
        from_items = self._parse_from_list() if self.accept_keyword("FROM") else []
        where = self.parse_expr() if self.accept_keyword("WHERE") else None
        return ast.Update(
            target, alias, assignments, from_items, where, span=self._token_span(start)
        )

    def _parse_assignment(self) -> ast.Assignment:
        target = self.parse_expr(70)
        self.expect(TokenKind.OPERATOR, "=")
        return ast.Assignment(target, self.parse_expr())

    def parse_delete(self) -> ast.Delete:
        start = self.expect_keyword("DELETE")
        self.accept_keyword("FROM")
        target = self.parse_qualified_name()
        alias = self._parse_optional_alias()
        using = self._parse_from_list() if self.accept_keyword("USING") else []
        where = self.parse_expr() if self.accept_keyword("WHERE") else None
        return ast.Delete(target, alias, using, where, span=self._token_span(start))

    def parse_merge(self) -> ast.Merge:
        start = self.expect_keyword("MERGE")
        self.accept_keyword("INTO")
        target = self.parse_qualified_name()
        alias = self._parse_optional_alias()
        self.expect_keyword("USING")
        source = self._parse_from_item()
        self.expect_keyword("ON")
        condition = self.parse_expr()
        clauses: list[ast.MergeClause] = []
        while self.accept_keyword("WHEN"):
            matched = bool(self.accept_keyword("MATCHED"))
            if not matched:
                self.accept_keyword("NOT")
                self.expect_keyword("MATCHED")
                if self.accept_keyword("BY"):
                    self.accept_keyword("SOURCE") or self.accept_keyword("TARGET")
            clause_condition = self.parse_expr() if self.accept_keyword("AND") else None
            self.expect_keyword("THEN")
            if self.accept_keyword("UPDATE"):
                self.expect_keyword("SET")
                action: ast.Node = ast.Update(
                    target, assignments=self.parse_comma_list(self._parse_assignment)
                )
            elif self.accept_keyword("DELETE"):
                action = ast.Delete(target)
            elif self.accept_keyword("INSERT"):
                columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
                if self.at_keyword("DEFAULT"):
                    self.advance()
                    self.expect_keyword("VALUES")
                    values: ast.Node = ast.DefaultValues()
                else:
                    values = (
                        self.parse_values() if self.at_keyword("VALUES") else ast.DefaultValues()
                    )
                action = ast.Insert(target, columns, values)
            else:
                raise ParseError("Expected MERGE action.", self.peek())
            clauses.append(ast.MergeClause(matched, clause_condition, action))
        return ast.Merge(target, alias, source, condition, clauses, span=self._token_span(start))

    def parse_truncate(self) -> ast.Truncate:
        self.expect_keyword("TRUNCATE")
        self.accept_keyword("TABLE")
        target = self.parse_qualified_name()
        while self.at_keyword("CASCADE", "RESTRICT", "CONTINUE", "IDENTITY", "RESTART"):
            self.advance()
        return ast.Truncate(target)

    def parse_copy(self) -> ast.DialectStatement:
        self.expect_keyword("COPY")
        args: dict[str, Any] = {}
        reference = None
        if self.at(TokenKind.PUNCT, "("):
            self.advance()
            args["query"] = self.parse_query()
            self.expect(TokenKind.PUNCT, ")")
        else:
            args["table"] = self.parse_qualified_name()
            reference = args["table"]
            if self.accept(TokenKind.PUNCT, "("):
                args["columns"] = self._parse_identifier_list()
        direction = self.advance().text.casefold() if self._is_name() else ""
        args["direction"] = direction
        args["source"] = self.advance().value if not self._statement_end() else None
        args.update(self._parse_options())
        refs = (
            [ast.RefHint(reference, "writes" if direction == "from" else "reads")]
            if reference is not None
            else []
        )
        return ast.DialectStatement(name="copy", args=args, references=refs)

    def parse_load(self) -> ast.DialectStatement:
        self.expect_keyword("LOAD")
        args: dict[str, Any] = {}
        if self.accept_keyword("DATA"):
            args["data"] = True
        if self.accept_keyword("LOCAL"):
            args["local"] = True
        if self.accept_keyword("INPATH"):
            args["path"] = self.advance().value
        if self.accept_keyword("OVERWRITE"):
            args["overwrite"] = True
        if self.accept_keyword("INTO"):
            self.accept_keyword("TABLE")
            args["table"] = self.parse_qualified_name()
        args.update(self._parse_options())
        table = args.get("table")
        refs = [ast.RefHint(table, "writes")] if isinstance(table, ast.QualifiedName) else []
        return ast.DialectStatement(name="load", args=args, references=refs)

    def parse_create(self) -> ast.Node:
        start = self.expect_keyword("CREATE")
        replace = False
        if self.accept_keyword("OR"):
            self.expect_keyword("REPLACE")
            replace = True
        temporary = bool(
            self.accept_keyword("TEMP")
            or self.accept_keyword("TEMPORARY")
            or self.accept_keyword("GLOBAL")
        )
        external = bool(self.accept_keyword("EXTERNAL"))
        secure = bool(self.accept_keyword("SECURE"))
        unique = False
        if self.accept_keyword("UNIQUE"):
            unique = True
        if self.at_keyword("MATERIALIZED"):
            self.advance()
            self.expect_keyword("VIEW")
            return self._parse_create_view(start, replace, materialized=True, secure=secure)
        if self.at_keyword("TABLE"):
            self.advance()
            return self._parse_create_table(start, temporary, external, replace)
        if self.at_keyword("VIEW"):
            self.advance()
            return self._parse_create_view(start, replace, secure=secure)
        if self.at_keyword("INDEX"):
            self.advance()
            name = self.parse_qualified_name()
            self.expect_keyword("ON")
            table = self.parse_qualified_name()
            self.expect(TokenKind.PUNCT, "(")
            columns = self.parse_comma_list(self.parse_expr)
            self.expect(TokenKind.PUNCT, ")")
            return ast.CreateIndex(name, table, columns, unique, span=self._token_span(start))
        if self.at_keyword("SEQUENCE"):
            self.advance()
            name = self.parse_qualified_name()
            options: dict[str, Any] = {}
            while not self._statement_end():
                if self.accept_keyword("START"):
                    self.accept_keyword("WITH")
                    options["start_with"] = self.parse_expr(80)
                elif self.accept_keyword("INCREMENT"):
                    self.accept_keyword("BY")
                    options["increment_by"] = self.parse_expr(80)
                elif self.accept_keyword("MINVALUE", "MAXVALUE", "CACHE"):
                    key = self.tokens[self.index - 1].text.casefold()
                    options[key] = self.parse_expr(80)
                elif self.accept_keyword("NO"):
                    options[f"no_{self.advance().text.casefold()}"] = True
                elif self.accept_keyword("CYCLE", "ORDER"):
                    options[self.tokens[self.index - 1].text.casefold()] = True
                elif self.accept_keyword("OWNED"):
                    self.expect_keyword("BY")
                    options["owned_by"] = self.parse_qualified_name()
                else:
                    key = self.advance().text.casefold()
                    options[key] = self.parse_expr(80) if not self._statement_end() else True
            return ast.CreateSequence(name, options, span=self._token_span(start))
        if self.at_keyword("SYNONYM"):
            self.advance()
            public = bool(self.accept_keyword("PUBLIC"))
            name = self.parse_qualified_name()
            if not self.accept_keyword("FOR"):
                self.expect_keyword("AS")
            return ast.CreateSynonym(
                name, self.parse_qualified_name(), public, span=self._token_span(start)
            )
        if self.at_keyword("SCHEMA") or self.at_keyword("DATABASE"):
            kind = self.advance().text.casefold()
            name = self.parse_qualified_name()
            cls = ast.CreateSchema if kind == "schema" else ast.CreateDatabase
            return cls(name, self._parse_options(), span=self._token_span(start))
        if any(self.at_keyword(kind) for kind in ("USER", "ROLE", "PRINCIPAL", "LOGIN", "GROUP")):
            kind = self.advance().text.casefold()
            if self.accept_keyword("IF"):
                self.expect_keyword("NOT")
                self.expect_keyword("EXISTS")
            return ast.CreatePrincipal(kind, self.parse_qualified_name(), self._parse_options())
        if any(
            self.at_keyword(kind)
            for kind in ("PROCEDURE", "FUNCTION", "MACRO", "TRIGGER", "PACKAGE", "TYPE")
        ):
            if self.at_keyword("PACKAGE") and self.at_keyword("BODY", 1):
                self.advance()
                self.advance()
                return self._parse_create_routine(start, "package_body")
            return self._parse_create_routine(start)
        raise ParseError("Unsupported CREATE statement.", self.peek())

    def _parse_create_table(
        self, start: Token, temporary: bool, external: bool, replace: bool
    ) -> ast.CreateTable:
        if_not_exists = False
        if self.accept_keyword("IF"):
            self.expect_keyword("NOT")
            self.expect_keyword("EXISTS")
            if_not_exists = True
        name = self.parse_qualified_name()
        columns: list[ast.ColumnDef] = []
        constraints: list[ast.TableConstraint] = []
        like = None
        if self.accept_keyword("LIKE"):
            like = self.parse_qualified_name()
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                if self._is_constraint_start():
                    constraints.append(self._parse_table_constraint())
                else:
                    columns.append(self._parse_column_def())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
                if self.at(TokenKind.PUNCT, ")"):
                    raise ParseError("Expected a table definition after comma.", self.peek())
            self.expect(TokenKind.PUNCT, ")")
        as_query = self.parse_query() if self.accept_keyword("AS") else None
        return ast.CreateTable(
            name,
            columns,
            constraints,
            as_query,
            like,
            temporary=temporary,
            external=external,
            replace=replace,
            if_not_exists=if_not_exists,
            options=self._parse_options(),
            span=self._token_span(start),
        )

    def _parse_create_view(
        self, start: Token, replace: bool, materialized: bool = False, secure: bool = False
    ) -> ast.CreateView:
        name = self.parse_qualified_name()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        self.expect_keyword("AS")
        query = self.parse_query()
        options: dict[str, Any] = {}
        if self.accept_keyword("WITH"):
            options["check_option"] = (
                self.advance().text.casefold()
                if self.at_keyword("LOCAL", "CASCADED")
                else "default"
            )
            self.accept_keyword("CHECK")
            self.accept_keyword("OPTION")
        return ast.CreateView(
            name,
            columns,
            query,
            materialized,
            replace,
            secure,
            options,
            span=self._token_span(start),
        )

    def _parse_column_def(self) -> ast.ColumnDef:
        name = self.parse_identifier()
        datatype = self.parse_data_type()
        constraints: list[ast.ColumnConstraint] = []
        default = None
        identity = None
        generated = None
        comment = None
        collation = None
        options: dict[str, Any] = {}
        while (
            not self.at(TokenKind.PUNCT, ",")
            and not self.at(TokenKind.PUNCT, ")")
            and not self.at(TokenKind.EOF)
        ):
            if self.accept_keyword("NOT"):
                self.expect_keyword("NULL")
                constraints.append(ast.ColumnConstraint("not_null"))
            elif self.accept_keyword("NULL"):
                constraints.append(ast.ColumnConstraint("null"))
            elif self.accept_keyword("PRIMARY"):
                self.expect_keyword("KEY")
                constraints.append(ast.ColumnConstraint("primary_key"))
            elif self.accept_keyword("UNIQUE"):
                constraints.append(ast.ColumnConstraint("unique"))
            elif self.accept_keyword("REFERENCES"):
                table = self.parse_qualified_name()
                cols = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
                constraints.append(
                    ast.ColumnConstraint("foreign_key", ref_table=table, ref_columns=cols)
                )
            elif self.accept_keyword("DEFAULT"):
                default = self.parse_expr(30)
            elif self.accept_keyword("CHECK"):
                self.expect(TokenKind.PUNCT, "(")
                expr = self.parse_expr()
                self.expect(TokenKind.PUNCT, ")")
                constraints.append(ast.ColumnConstraint("check", expr=expr))
            elif self.accept_keyword("CONSTRAINT"):
                cname = self.parse_identifier()
                constraints.append(ast.ColumnConstraint("named", name=cname))
            elif self.accept_keyword("GENERATED"):
                self.accept_keyword("ALWAYS")
                if self.accept_keyword("BY"):
                    self.expect_keyword("DEFAULT")
                self.accept_keyword("AS")
                if self.accept_keyword("IDENTITY"):
                    identity = ast.DialectExpr(name="identity", args=self._parse_options())
                else:
                    self.expect(TokenKind.PUNCT, "(")
                    generated = self.parse_expr()
                    self.expect(TokenKind.PUNCT, ")")
                    self.accept_keyword("STORED") or self.accept_keyword("VIRTUAL")
            elif self.accept_keyword("IDENTITY"):
                identity = ast.DialectExpr(name="identity", args=self._parse_options())
            elif self.accept_keyword("COLLATE"):
                collation = self.parse_qualified_name()
            elif self.accept_keyword("COMMENT"):
                token = self.expect(TokenKind.STRING)
                comment = token.value
            elif self.at_keyword("OPTIONS"):
                options = self._parse_options()
            else:
                break
        return ast.ColumnDef(
            name,
            datatype,
            constraints,
            default,
            identity,
            generated,
            comment,
            collation,
            options,
        )

    def _is_constraint_start(self) -> bool:
        return any(
            self.at_keyword(word)
            for word in ("CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK")
        )

    def _parse_table_constraint(self) -> ast.TableConstraint:
        name = self.parse_identifier() if self.accept_keyword("CONSTRAINT") else None
        if self.accept_keyword("PRIMARY"):
            self.expect_keyword("KEY")
            kind = "primary_key"
            columns = self._parse_identifier_list_in_parens()
            return ast.TableConstraint(kind, name, columns)
        if self.accept_keyword("UNIQUE"):
            return ast.TableConstraint("unique", name, self._parse_identifier_list_in_parens())
        if self.accept_keyword("FOREIGN"):
            self.expect_keyword("KEY")
            columns = self._parse_identifier_list_in_parens()
            self.expect_keyword("REFERENCES")
            ref = self.parse_qualified_name()
            ref_columns = self._parse_identifier_list_in_parens()
            return ast.TableConstraint("foreign_key", name, columns, ref, ref_columns)
        if self.accept_keyword("CHECK"):
            self.expect(TokenKind.PUNCT, "(")
            expr = self.parse_expr()
            self.expect(TokenKind.PUNCT, ")")
            return ast.TableConstraint("check", name, expr=expr)
        raise ParseError("Expected table constraint.", self.peek())

    def parse_alter(self) -> ast.Alter:
        self.expect_keyword("ALTER")
        object_kind = self.advance().text.casefold()
        name = self.parse_qualified_name()
        actions: list[ast.Node] = []
        if self.accept_keyword("ADD"):
            if self.accept_keyword("COLUMN"):
                actions.append(self._parse_column_def())
            else:
                actions.append(self._parse_table_constraint())
        elif self.accept_keyword("DROP"):
            dropped_kind = (
                self.advance().text.casefold()
                if self.at_keyword("COLUMN", "CONSTRAINT")
                else "column"
            )
            actions.append(ast.Drop(object_kind=dropped_kind, names=[self.parse_qualified_name()]))
        elif self.accept_keyword("RENAME"):
            renamed_kind = (
                self.advance().text.casefold()
                if self.at_keyword("COLUMN", "CONSTRAINT")
                else object_kind
            )
            old_name = self.parse_qualified_name()
            self.accept_keyword("TO")
            actions.append(ast.Rename(renamed_kind, old_name, self.parse_qualified_name()))
        else:
            actions.append(
                ast.DialectStatement(name="alter_action", args={"text": self._consume_to_end()})
            )
        return ast.Alter(object_kind, name, actions)

    def parse_drop(self) -> ast.Drop:
        self.expect_keyword("DROP")
        kind = self.advance().text.casefold()
        if kind == "materialized" and self.accept_keyword("VIEW"):
            kind = "materialized_view"
        if_exists = False
        if self.accept_keyword("IF"):
            self.expect_keyword("EXISTS")
            if_exists = True
        names = self.parse_comma_list(self.parse_qualified_name)
        cascade = bool(self.accept_keyword("CASCADE"))
        self.accept_keyword("RESTRICT")
        return ast.Drop(kind, names, if_exists, cascade)

    def parse_comment(self) -> ast.CommentOn:
        self.expect_keyword("COMMENT")
        self.expect_keyword("ON")
        kind = self.advance().text.casefold()
        name = self.parse_qualified_name()
        self.expect_keyword("IS")
        token = self.expect(TokenKind.STRING)
        return ast.CommentOn(kind, name, token.value)

    def parse_rename(self) -> ast.Rename:
        self.expect_keyword("RENAME")
        kind = self.advance().text.casefold() if not self.at_keyword("TO") else "object"
        name = self.parse_qualified_name()
        self.expect_keyword("TO")
        return ast.Rename(kind, name, self.parse_qualified_name())

    def parse_grant(self) -> ast.Grant:
        self.expect_keyword("GRANT")
        privileges = self.parse_comma_list(lambda: self.advance().text)
        if len(privileges) == 1 and privileges[0].casefold() == "all":
            self.accept_keyword("PRIVILEGES")
        object_kind = "object"
        if self.accept_keyword("ON"):
            object_kind = self.advance().text.casefold()
            object_name = self.parse_qualified_name()
        else:
            object_name = ast.QualifiedName()
        self.expect_keyword("TO")
        grantees = self.parse_comma_list(self.parse_qualified_name)
        grant_option = False
        if self.accept_keyword("WITH"):
            self.expect_keyword("GRANT")
            self.expect_keyword("OPTION")
            grant_option = True
        return ast.Grant(privileges, object_kind, object_name, grantees, grant_option)

    def parse_revoke(self) -> ast.Revoke:
        self.expect_keyword("REVOKE")
        grant_option = False
        if self.accept_keyword("GRANT"):
            self.expect_keyword("OPTION")
            self.expect_keyword("FOR")
            grant_option = True
        privileges = self.parse_comma_list(lambda: self.advance().text)
        if len(privileges) == 1 and privileges[0].casefold() == "all":
            self.accept_keyword("PRIVILEGES")
        self.expect_keyword("ON")
        object_kind = self.advance().text.casefold()
        object_name = self.parse_qualified_name()
        self.expect_keyword("FROM")
        grantees = self.parse_comma_list(self.parse_qualified_name)
        return ast.Revoke(privileges, object_kind, object_name, grantees, grant_option)

    def _parse_create_routine(
        self, start: Token, routine_kind: str | None = None
    ) -> ast.CreateRoutine:
        kind = routine_kind or self.advance().text.casefold()
        name = self.parse_qualified_name()
        params: list[ast.RoutineParam] = []
        options: dict[str, Any] = {}
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")"):
                mode = (
                    self.advance().text.casefold()
                    if self.at_keyword("IN") or self.at_keyword("OUT")
                    else None
                )
                pname = self.parse_identifier()
                dtype = self.parse_data_type()
                default = self.parse_expr() if self.accept_keyword("DEFAULT") else None
                params.append(ast.RoutineParam(pname, mode, dtype, default))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        if kind == "trigger":
            trigger_parts: list[str] = []
            while not self.at_keyword("BEGIN", "AS") and not self.at(TokenKind.TERMINATOR, ";"):
                if self.accept_keyword("ON"):
                    options["trigger_on"] = self.parse_qualified_name()
                elif self.at(TokenKind.EOF):
                    break
                else:
                    trigger_parts.append(self.advance().text)
            options["trigger_clause"] = " ".join(trigger_parts)
        returns = self.parse_data_type() if self.accept_keyword("RETURNS") else None
        language = self.advance().text if self.accept_keyword("LANGUAGE") else None
        self.accept_keyword("AS")
        body: ast.Node | str | None = None
        if self.at(TokenKind.DOLLAR_BODY) or self.at(TokenKind.STRING):
            body = self.advance().value
        elif self.at_keyword("BEGIN"):
            body = self.parse_block()
        elif kind in {"package", "package_body", "type"}:
            body = self._consume_to_end()
        elif kind == "macro" and self.accept(TokenKind.PUNCT, "("):
            body = self.parse_query()
            self.expect(TokenKind.PUNCT, ")")
        elif self.accept_keyword("RETURN"):
            body = ast.Return(self.parse_expr())
        else:
            body = self.parse_query() if self.at_keyword("SELECT") else None
        return ast.CreateRoutine(
            kind,
            name,
            params,
            returns,
            language,
            body,
            options,
            span=self._token_span(start),
        )

    def _parse_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        while (
            self.accept_keyword("WITH")
            or self.accept_keyword("OPTIONS")
            or self.accept_keyword("USING")
        ):
            if self.accept(TokenKind.PUNCT, "("):
                while not self.at(TokenKind.PUNCT, ")"):
                    key = self.advance().text
                    value: Any = True
                    if self.accept(TokenKind.OPERATOR, "="):
                        value = self.advance().value
                    options[key] = value
                    if not self.accept(TokenKind.PUNCT, ","):
                        break
                self.expect(TokenKind.PUNCT, ")")
            else:
                key = self.advance().text
                options[key] = (
                    self.advance().value if self._is_name() or self.at(TokenKind.STRING) else True
                )
        return options

    def _parse_identifier_list(self) -> list[ast.Identifier]:
        values = (
            self.parse_comma_list(self.parse_identifier)
            if not self.at(TokenKind.PUNCT, ")")
            else []
        )
        self.expect(TokenKind.PUNCT, ")")
        return values

    def _parse_identifier_list_in_parens(self) -> list[ast.Identifier]:
        self.expect(TokenKind.PUNCT, "(")
        return self._parse_identifier_list()

    def parse_block(self) -> ast.Block:
        start = self.expect_keyword("BEGIN")
        self.accept_keyword("ATOMIC")
        declarations: list[ast.Node] = []
        while self.accept_keyword("DECLARE"):
            declarations.append(self._parse_declare_after_keyword())
            self.accept(TokenKind.TERMINATOR)
        statements: list[ast.Node] = []
        while not self.at_keyword("END") and not self.at(TokenKind.EOF):
            if self.at_keyword("EXCEPTION"):
                break
            if self.at(TokenKind.TERMINATOR):
                self.advance()
                continue
            statement_start = self.index
            try:
                statements.append(self.parse_statement())
            except ParseLimitError as error:
                if error.code == "parser.time_limit":
                    raise
                self.issues.append(
                    ast.Issue("error", error.code, str(error), self._token_span(error.token))
                )
                statements.append(
                    ast.Unparsed(
                        text=self._slice_tokens(statement_start, self.index),
                        reason=str(error),
                        span=self._token_span(self.tokens[statement_start]),
                    )
                )
                self._recover(statement_start)
            except ParseError as error:
                self.issues.append(
                    ast.Issue("error", "parser.syntax", str(error), self._token_span(error.token))
                )
                statements.append(
                    ast.Unparsed(
                        text=self._slice_tokens(statement_start, self.index),
                        reason=str(error),
                        span=self._token_span(self.tokens[statement_start]),
                    )
                )
                self._recover(statement_start)
            self.accept(TokenKind.TERMINATOR)
        handlers: list[ast.ExceptionHandler] = []
        if self.accept_keyword("EXCEPTION"):
            while self.accept_keyword("WHEN"):
                conditions = [self.advance().text]
                while self.accept_keyword("OR"):
                    conditions.append(self.advance().text)
                self.expect_keyword("THEN")
                handler_statements = self._parse_statement_sequence({"WHEN", "END"})
                handlers.append(ast.ExceptionHandler(conditions, handler_statements))
        self.expect_keyword("END")
        label = self.parse_identifier().value if self._is_name() else None
        return ast.Block(label, declarations, statements, handlers, span=self._token_span(start))

    def parse_declare(self) -> ast.Node:
        self.expect_keyword("DECLARE")
        return self._parse_declare_after_keyword()

    def _parse_declare_after_keyword(self) -> ast.Node:
        if self.at_keyword("CONTINUE", "EXIT"):
            action = self.advance().text.casefold()
            self.expect_keyword("HANDLER")
            self.expect_keyword("FOR")
            condition_tokens = []
            while (
                not self.at_keyword(
                    "BEGIN",
                    "SET",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "MERGE",
                    "SELECT",
                    "CALL",
                    "EXEC",
                    "EXECUTE",
                    "SIGNAL",
                    "RETURN",
                    "RAISE",
                    "OPEN",
                    "FETCH",
                    "CLOSE",
                    "COMMIT",
                    "ROLLBACK",
                    "IF",
                    "WHILE",
                    "LOOP",
                    "FOR",
                    "CASE",
                    "REPEAT",
                    "LEAVE",
                    "EXIT",
                    "CONTINUE",
                )
                and not self.at(TokenKind.EOF)
                and not self.at(TokenKind.TERMINATOR, ";")
            ):
                condition_tokens.append(self.advance().text)
            if self.at_keyword("BEGIN"):
                statements = self.parse_block().statements
            elif not self._statement_end():
                statements = [self.parse_statement()]
            else:
                statements = []
            return ast.HandlerDecl(" ".join([action, *condition_tokens]), statements)
        name = self.parse_identifier()
        if self.accept_keyword("CURSOR"):
            self.expect_keyword("FOR")
            return ast.CursorDecl(name, self.parse_query())
        datatype = self.parse_data_type()
        default = None
        if self.accept_keyword("DEFAULT") or self.accept_keyword("VALUE"):
            default = self.parse_expr()
        elif self.accept(TokenKind.OPERATOR, ":=") or self.accept(TokenKind.OPERATOR, "="):
            default = self.parse_expr()
        return ast.Declare(name, datatype, default)

    def parse_if(self) -> ast.If:
        self.expect_keyword("IF")
        condition = self.parse_expr()
        self.expect_keyword("THEN")
        branches = [(condition, self._parse_statement_sequence({"ELSIF", "ELSEIF", "ELSE", "END"}))]
        while self.accept_keyword("ELSIF") or self.accept_keyword("ELSEIF"):
            condition = self.parse_expr()
            self.expect_keyword("THEN")
            branches.append(
                (condition, self._parse_statement_sequence({"ELSIF", "ELSEIF", "ELSE", "END"}))
            )
        else_branch = []
        if self.accept_keyword("ELSE"):
            else_branch = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.accept_keyword("IF")
        return ast.If(branches, else_branch)

    def parse_while(self) -> ast.While:
        self.expect_keyword("WHILE")
        condition = self.parse_expr()
        if not (self.accept_keyword("DO") or self.accept_keyword("LOOP")):
            raise ParseError("Expected DO or LOOP.", self.peek())
        body = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.accept_keyword("WHILE")
        return ast.While(condition, body)

    def parse_loop(self) -> ast.Loop:
        self.expect_keyword("LOOP")
        body = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.accept_keyword("LOOP")
        return ast.Loop(statements=body)

    def parse_for(self) -> ast.ForLoop:
        self.expect_keyword("FOR")
        variable = self.parse_identifier()
        self.expect_keyword("IN")
        if self.at_keyword("SELECT") or self.at_keyword("WITH"):
            iterable: ast.Node = self.parse_query()
        else:
            reverse = bool(self.accept_keyword("REVERSE"))
            start = self.parse_expr(51)
            if self.accept(TokenKind.OPERATOR, ".."):
                end = self.parse_expr(51)
                iterable = ast.Range(start, end, reverse=reverse)
            else:
                iterable = start
        if not (self.accept_keyword("DO") or self.accept_keyword("LOOP")):
            raise ParseError("Expected DO or LOOP.", self.peek())
        body = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.accept_keyword("FOR") or self.accept_keyword("LOOP")
        return ast.ForLoop(variable, iterable, body)

    def parse_repeat(self) -> ast.Repeat:
        self.expect_keyword("REPEAT")
        body = self._parse_statement_sequence({"UNTIL"})
        self.expect_keyword("UNTIL")
        condition = self.parse_expr()
        self.accept_keyword("END")
        self.accept_keyword("REPEAT")
        return ast.Repeat(body, condition)

    def _parse_statement_sequence(self, stop: set[str]) -> list[ast.Node]:
        statements: list[ast.Node] = []
        while not self.at(TokenKind.EOF) and not any(self.at_keyword(word) for word in stop):
            if self.at(TokenKind.TERMINATOR):
                self.advance()
            else:
                statements.append(self.parse_statement())
                self.accept(TokenKind.TERMINATOR)
        return statements

    def parse_set(self) -> ast.SetStatement:
        self.expect_keyword("SET")
        scope = None
        if (
            self.accept_keyword("LOCAL")
            or self.accept_keyword("SESSION")
            or self.accept_keyword("GLOBAL")
        ):
            scope = self.tokens[self.index - 1].text.casefold()
        items = self.parse_comma_list(self._parse_set_item)
        return ast.SetStatement(scope, items)

    def _parse_set_item(self) -> ast.SetItem:
        name = self.parse_expr(70)
        if not (self.accept(TokenKind.OPERATOR, "=") or self.accept(TokenKind.OPERATOR, ":=")):
            raise ParseError("Expected assignment operator.", self.peek())
        return ast.SetItem(name, self.parse_expr())

    def parse_call(self) -> ast.Call:
        self.expect_keyword("CALL")
        return self.parse_call_after_keyword()

    def parse_call_after_keyword(self) -> ast.Call:
        name = self.parse_qualified_name()
        args = []
        if self.accept(TokenKind.PUNCT, "("):
            args = (
                self.parse_comma_list(self.parse_expr) if not self.at(TokenKind.PUNCT, ")") else []
            )
            self.expect(TokenKind.PUNCT, ")")
        return ast.Call(name, args)

    def parse_dynamic_sql(self) -> ast.DynamicSql:
        keyword = self.advance()
        if keyword.text.upper() == "PREPARE":
            name = self.parse_identifier()
            self.expect_keyword("FROM")
            sql_expr = self.parse_expr(70)
            parsed = self._parse_embedded_sql(sql_expr)
            return ast.DynamicSql(
                sql_expr=sql_expr,
                using=[ast.ColumnRef(ast.QualifiedName(parts=[name]))],
                parsed=parsed,
                span=self._token_span(keyword),
            )
        immediate = bool(self.accept_keyword("IMMEDIATE"))
        if keyword.text.upper() == "EXECUTE" and not immediate and self._is_name():
            call = self.parse_call_after_keyword()
            using = []
            if self.accept_keyword("USING"):
                using = self.parse_comma_list(self.parse_expr)
            return ast.DynamicSql(
                sql_expr=call,
                using=using,
                parsed=[call],
                span=self._token_span(keyword),
            )
        sql_expr = self.parse_expr(70)
        into: list[ast.Identifier] = []
        using: list[ast.Node] = []
        if self.accept_keyword("INTO"):
            into = self.parse_comma_list(self.parse_identifier)
        if self.accept_keyword("USING"):
            using = self.parse_comma_list(self.parse_expr)
        parsed = self._parse_embedded_sql(sql_expr)
        return ast.DynamicSql(sql_expr, into, using, parsed, span=self._token_span(keyword))

    def _parse_embedded_sql(self, sql_expr: ast.Node) -> list[ast.Node]:
        if isinstance(sql_expr, ast.Literal) and sql_expr.type in {"string", "national_string"}:
            parser = type(self)()
            nested = parser.parse(sql_expr.value, self.rel_path)
            self.issues.extend(nested.issues)
            return nested.statements
        return []

    def parse_open_cursor(self) -> ast.OpenCursor:
        self.expect_keyword("OPEN")
        name = self.parse_identifier()
        args = []
        if self.accept_keyword("USING"):
            args = self.parse_comma_list(self.parse_expr)
        return ast.OpenCursor(name, args)

    def parse_fetch(self) -> ast.Fetch:
        self.expect_keyword("FETCH")
        self.accept_keyword("NEXT")
        self.accept_keyword("FROM")
        name = self.parse_identifier()
        into = []
        if self.accept_keyword("INTO"):
            into = self.parse_comma_list(self.parse_identifier)
        return ast.Fetch(name, into)

    def parse_close_cursor(self) -> ast.CloseCursor:
        self.expect_keyword("CLOSE")
        return ast.CloseCursor(self.parse_identifier())

    def _consume_to_end(self) -> str:
        start = self.peek().offset
        while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR):
            self.advance()
        end = self.peek().offset
        return self.text[start:end]

    def _is_name(self) -> bool:
        return self.peek().kind in {TokenKind.IDENT, TokenKind.QUOTED_IDENT}

    def _can_be_alias(self) -> bool:
        if not self._is_name():
            return False
        return self.peek().text.upper() not in {
            "INTO",
            "FROM",
            "WHERE",
            "GROUP",
            "HAVING",
            "QUALIFY",
            "ORDER",
            "LIMIT",
            "MINUS",
            "OFFSET",
            "FETCH",
            "UNION",
            "INTERSECT",
            "EXCEPT",
            "JOIN",
            "LEFT",
            "RIGHT",
            "FULL",
            "INNER",
            "CROSS",
            "SEMI",
            "ANTI",
            "ON",
            "USING",
            "WITH",
            "WINDOW",
            "FOR",
            "TABLESAMPLE",
            "SAMPLE",
            "PIVOT",
            "UNPIVOT",
            "WHEN",
            "THEN",
            "ELSE",
            "DO",
            "LOOP",
            "END",
            "SET",
            "RETURNING",
        }

    def _statement_end(self) -> bool:
        return self.at(TokenKind.EOF) or self.at(TokenKind.TERMINATOR) or self.at_keyword("END")

    def _token_span(self, token: Token) -> ast.Span:
        newlines = token.text.count("\n")
        if newlines:
            return ast.Span(
                token.line,
                token.col,
                token.line + newlines,
                len(token.text.rsplit("\n", 1)[-1]) + 1,
            )
        return ast.Span(token.line, token.col, token.line, token.col + len(token.text))

    def _range_span(self, first: Token, last: Token) -> ast.Span:
        return ast.Span(
            first.line,
            first.col,
            self._token_span(last).end_line,
            self._token_span(last).end_col,
        )

    def _slice_tokens(self, start: int, end: int) -> str:
        if not self.tokens or start >= len(self.tokens):
            return ""
        first = self.tokens[start].offset
        last = self.tokens[min(end, len(self.tokens) - 1)]
        return self.text[first : last.offset + len(last.text)]

    def is_statement_end(self, token: Token) -> bool:
        return token.kind is TokenKind.TERMINATOR

    def _recover(self, start: int) -> None:
        if self.index <= start:
            self.index = min(start + 1, len(self.tokens) - 1)
        depth = 0
        statement_starts = {
            "SELECT",
            "WITH",
            "INSERT",
            "UPDATE",
            "DELETE",
            "MERGE",
            "CREATE",
            "ALTER",
            "DROP",
            "GRANT",
            "REVOKE",
            "TRUNCATE",
            "BEGIN",
            "CALL",
        }
        previous_line = self.tokens[self.index - 1].line if self.index else 1
        while self.index < len(self.tokens) - 1:
            token = self.tokens[self.index]
            if token.kind is TokenKind.PUNCT and token.text == "(":
                depth += 1
            elif token.kind is TokenKind.PUNCT and token.text == ")":
                depth = max(0, depth - 1)
            elif self.is_statement_end(token) and depth == 0:
                self.index += 1
                return
            elif (
                depth == 0
                and token.line > previous_line
                and token.kind is TokenKind.IDENT
                and token.text.upper() in statement_starts
            ):
                return
            previous_line = token.line
            self.index += 1

    def _check_time(self) -> None:
        if self.index < self._next_time_check:
            return
        self._next_time_check = self.index + self.time_check_interval
        if self.clock() - self._started > self.file_time_limit_seconds:
            raise ParseLimitError(
                f"Parsing exceeded {self.file_time_limit_seconds:g} seconds.",
                self.tokens[min(self.index, len(self.tokens) - 1)],
                "parser.time_limit",
            )
