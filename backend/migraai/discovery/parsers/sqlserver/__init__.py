from __future__ import annotations

from typing import Any

from migraai.discovery import ast
from migraai.discovery.lexer import Token, TokenKind
from migraai.discovery.parser_base import ParseError, ParserBase

from .lexer import LEXER_CONFIG, TSqlLexer
from .nodes import TSqlDelete, TSqlInsert, TSqlMerge, TSqlUpdate

_TSQL_START_WORDS = frozenset(
    {
        "SELECT",
        "VALUES",
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "IF",
        "ELSE",
        "WHILE",
        "BEGIN",
        "END",
        "DECLARE",
        "SET",
        "EXEC",
        "EXECUTE",
        "RETURN",
        "PRINT",
        "THROW",
        "RAISERROR",
        "CREATE",
        "ALTER",
        "DROP",
        "TRUNCATE",
        "OPTION",
        "OUTPUT",
        "BREAK",
        "CONTINUE",
        "COMMIT",
        "ROLLBACK",
        "SAVE",
        "USE",
        "GRANT",
        "REVOKE",
        "DENY",
        "GOTO",
        "WAITFOR",
        "OPEN",
        "FETCH",
        "CLOSE",
        "DEALLOCATE",
        "WITH",
    }
)

_TSQL_BUILTINS = frozenset(
    {
        "GETDATE",
        "GETUTCDATE",
        "SYSDATETIME",
        "SYSUTCDATETIME",
        "DATEADD",
        "DATEDIFF",
        "DATEPART",
        "DATENAME",
        "EOMONTH",
        "FORMAT",
        "ISNULL",
        "IIF",
        "CHOOSE",
        "LEN",
        "DATALENGTH",
        "CHARINDEX",
        "PATINDEX",
        "REPLACE",
        "STUFF",
        "STRING_AGG",
        "STRING_SPLIT",
        "LEFT",
        "RIGHT",
        "LTRIM",
        "RTRIM",
        "TRIM",
        "NEWID",
        "NEWSEQUENTIALID",
        "SCOPE_IDENTITY",
        "IDENT_CURRENT",
        "OBJECT_ID",
        "DB_NAME",
        "DB_ID",
        "SUSER_SNAME",
        "ERROR_MESSAGE",
        "ERROR_NUMBER",
        "ERROR_SEVERITY",
        "ERROR_STATE",
        "ERROR_LINE",
        "ERROR_PROCEDURE",
        "RAISERROR",
        "ROW_NUMBER",
        "NTILE",
        "HASHBYTES",
        "CONCAT_WS",
        "TRANSLATE",
        "JSON_VALUE",
        "JSON_QUERY",
        "JSON_MODIFY",
        "OPENJSON",
        "ISJSON",
        "TRY_PARSE",
        "PARSE",
        "COUNT_BIG",
        "GROUPING",
        "GROUPING_ID",
        "APPROX_COUNT_DISTINCT",
        "XACT_STATE",
        "@@IDENTITY",
    }
)


def _identifier_name(value: str, span: ast.Span | None = None) -> ast.QualifiedName:
    return ast.QualifiedName(parts=[ast.Identifier(value=value, span=span or ast.Span())])


class TSqlParserBase(ParserBase):
    builtin_functions = ParserBase.ansi_builtins | _TSQL_BUILTINS
    binary_ops = {
        **ParserBase.binary_ops,
        "+": (50, 51, "+"),
        "-": (50, 51, "-"),
        "*": (60, 61, "*"),
        "/": (60, 61, "/"),
        "%": (60, 61, "%"),
    }
    _inside_trigger = False

    def __init__(self, text: str | None = None, rel_path: str = "") -> None:
        super().__init__(text, rel_path, lexer_config=LEXER_CONFIG)
        self._inside_trigger = False

    def parse(self, text: str | None = None, rel_path: str | None = None) -> ast.ParseResult:
        if text is not None:
            self.text = text
        if rel_path is not None:
            self.rel_path = rel_path
        lexer_issues: list[ast.Issue] = []
        if text is not None or not self.tokens:
            lexer = TSqlLexer(self.text, self.lexer_config)
            self.tokens = lexer.tokenize()
            lexer_issues = list(lexer.issues)
        result = super().parse(None, None)
        if lexer_issues:
            result.issues[0:0] = lexer_issues
        self._inside_trigger = False
        return result

    def _can_be_alias(self) -> bool:
        return super()._can_be_alias() and self.peek().text.upper() not in _TSQL_START_WORDS

    def parse_qualified_name(self) -> ast.QualifiedName:
        start = self.peek()
        parts = [self.parse_identifier()]
        while True:
            if self.accept(TokenKind.PUNCT, "."):
                if self.accept(TokenKind.OPERATOR, "*"):
                    parts.append(ast.Identifier(value="*"))
                    break
                parts.append(self.parse_identifier())
            elif self.accept(TokenKind.OPERATOR, ".."):
                parts.extend((ast.Identifier(value=""), self.parse_identifier()))
            else:
                break
        return ast.QualifiedName(
            parts=parts, span=self._range_span(start, self.tokens[self.index - 1])
        )

    def parse_primary_ext(self) -> ast.Node | None:
        if self.at(TokenKind.PARAM):
            token = self.advance()
            return ast.Variable(token.text, span=self._token_span(token))
        return None

    def parse_statement_ext(self) -> ast.Node | None:
        if self.at_keyword("EXEC", "EXECUTE"):
            return self._parse_exec()
        if self.at_keyword("DECLARE"):
            return self._parse_tsql_declare()
        if self.at_keyword("SET"):
            return self._parse_tsql_set()
        if self.at_keyword("IF"):
            return self.parse_if()
        if self.at_keyword("WHILE"):
            return self.parse_while()
        if self.at_keyword("BEGIN"):
            if self.at_keyword("TRAN", 1) or self.at_keyword("TRANSACTION", 1):
                self.advance()
                self.advance()
                name = self.parse_identifier() if self._is_name() else None
                return ast.DialectStatement(
                    name="begin_transaction", args={"name": name.value if name else None}
                )
            if self.at_keyword("TRY", 1):
                return self._parse_try_catch()
        if self.at_keyword("PRINT"):
            start = self.advance()
            return ast.DialectStatement(
                name="print", args={"expr": self.parse_expr()}, span=self._token_span(start)
            )
        if self.at_keyword("THROW"):
            return self._parse_throw()
        if self.at_keyword("RAISERROR"):
            start = self.peek()
            expr = self.parse_expr()
            if self.accept_keyword("WITH"):
                options = [self.advance().text]
                while self.accept(TokenKind.PUNCT, ","):
                    options.append(self.advance().text)
                if isinstance(expr, ast.FunctionCall):
                    expr.args.append(
                        ast.NamedArg(
                            ast.Identifier(value="with"), ast.Literal("string", ",".join(options))
                        )
                    )
            return ast.Raise(expr=expr, span=self._token_span(start))
        if self.at_keyword("BREAK"):
            self.advance()
            return ast.Exit()
        if self.at_keyword("CONTINUE"):
            self.advance()
            return ast.Continue()
        if self.at_keyword("GOTO"):
            self.advance()
            return ast.DialectStatement(name="goto", args={"label": self.parse_identifier()})
        if self.at_keyword("WAITFOR"):
            start = self.advance()
            mode = self.advance().text.casefold() if self.at_keyword("DELAY", "TIME") else "delay"
            value = self.parse_expr()
            return ast.DialectStatement(
                name="waitfor", args={"mode": mode, "value": value}, span=self._token_span(start)
            )
        if self.at_keyword("DEALLOCATE"):
            self.advance()
            self.accept_keyword("CURSOR")
            return ast.DialectStatement(
                name="deallocate_cursor", args={"name": self.parse_identifier()}
            )
        if self.at_keyword("OPEN"):
            self.advance()
            name = self.parse_identifier()
            args: list[ast.Node] = []
            if self.accept(TokenKind.PUNCT, "("):
                args = (
                    self.parse_comma_list(self.parse_expr)
                    if not self.at(TokenKind.PUNCT, ")")
                    else []
                )
                self.expect(TokenKind.PUNCT, ")")
            return ast.OpenCursor(name=name, args=args)
        if self.at_keyword("FETCH"):
            return self.parse_fetch()
        if self.at_keyword("CLOSE"):
            self.advance()
            self.accept_keyword("CURSOR")
            return ast.CloseCursor(name=self.parse_identifier())
        if self.at_keyword("COMMIT"):
            self.advance()
            self.accept_keyword("TRANSACTION") or self.accept_keyword("TRAN")
            name = self.parse_identifier() if self._is_name() else None
            return ast.DialectStatement(
                name="commit_transaction", args={"name": name.value if name else None}
            )
        if self.at_keyword("ROLLBACK"):
            self.advance()
            self.accept_keyword("TRANSACTION") or self.accept_keyword("TRAN")
            name = self.parse_identifier() if self._is_name() else None
            return ast.Rollback(savepoint=name)
        if self.at_keyword("SAVE"):
            self.advance()
            self.accept_keyword("TRANSACTION") or self.accept_keyword("TRAN")
            return ast.DialectStatement(
                name="save_transaction", args={"name": self.parse_identifier()}
            )
        if self.at_keyword("DENY"):
            return self._parse_deny()
        if self.at_keyword("ALTER") and self.at_keyword("ROLE", 1):
            return self._parse_alter_role()
        if self.at_keyword("ALTER") and (
            self.at_keyword("PROCEDURE", 1) or self.at_keyword("PROC", 1)
        ):
            return self._parse_alter_routine()
        if self.at_keyword("ALTER") and self.at_keyword("FUNCTION", 1):
            return self._parse_alter_routine()
        if self.at_keyword("CREATE") or self.at_keyword("ALTER"):
            return self.parse_create() if self.at_keyword("CREATE") else None
        if self.at_keyword("TRUNCATE"):
            return self.parse_truncate()
        return None

    def parse_query(self) -> ast.Query:
        query = super().parse_query()
        if self.accept_keyword("OPTION"):
            self.expect(TokenKind.PUNCT, "(")
            hints: list[str] = []
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                start = self.index
                depth = 0
                while not self.at(TokenKind.EOF):
                    if self.at(TokenKind.PUNCT, "("):
                        depth += 1
                    elif self.at(TokenKind.PUNCT, ")"):
                        if depth == 0:
                            break
                        depth -= 1
                    if depth == 0 and self.at(TokenKind.PUNCT, ","):
                        break
                    self.advance()
                hints.append(" ".join(token.text for token in self.tokens[start : self.index]))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
            select = query.body
            if isinstance(select, ast.Select):
                select.hints.extend(hints)
        return query

    def parse_select(self) -> ast.Select:
        select = super().parse_select()
        for item in select.projections:
            expr = item.expr
            if (
                isinstance(expr, ast.Binary)
                and expr.op in {"=", "+=", "-="}
                and isinstance(expr.left, ast.Variable)
            ):
                item.expr = ast.Assign(target=expr.left, value=expr.right)
        return select

    def _parse_from_item(self) -> ast.Node:
        start = self.peek()
        if self.at(TokenKind.PARAM):
            token = self.advance()
            name = ast.Variable(token.text, span=self._token_span(token))
            alias = self._parse_optional_alias()
            return ast.DialectExpr(
                name="table_variable",
                args={"name": name, "alias": alias},
                span=self._token_span(start),
            )
        if self._inside_trigger and self.at_keyword("inserted", "deleted"):
            token = self.advance()
            alias = self._parse_optional_alias()
            return ast.DialectExpr(
                name="trigger_pseudo_table",
                args={"name": token.text.casefold(), "alias": alias},
                span=self._token_span(start),
            )
        return super()._parse_from_item()

    def _parse_join(self) -> ast.Node:
        left = self._parse_from_item()
        while True:
            if self.at_keyword("CROSS") and self.at_keyword("APPLY", 1):
                self.advance()
                self.advance()
                right = self._parse_from_item()
                left = ast.Join(join_type="cross_apply", left=left, right=right)
                continue
            if self.at_keyword("OUTER") and self.at_keyword("APPLY", 1):
                self.advance()
                self.advance()
                right = self._parse_from_item()
                left = ast.Join(join_type="outer_apply", left=left, right=right)
                continue
            if self.at_keyword("JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS"):
                left = self._parse_standard_join_tail(left)
                continue
            break
        return left

    def _parse_standard_join_tail(self, left: ast.Node) -> ast.Node:
        natural = bool(self.accept_keyword("NATURAL"))
        join_kind = "inner"
        if self.at_keyword("SEMI", "ANTI"):
            join_kind = self.advance().text.casefold()
        elif self.at_keyword("LEFT", "RIGHT", "FULL"):
            join_kind = self.advance().text.casefold()
            self.accept_keyword("OUTER")
        elif self.at_keyword("CROSS"):
            join_kind = self.advance().text.casefold()
        elif self.accept_keyword("INNER"):
            join_kind = "inner"
        self.expect_keyword("JOIN")
        right = self._parse_from_item()
        condition = self.parse_expr() if self.accept_keyword("ON") else None
        using = []
        if self.accept_keyword("USING"):
            self.expect(TokenKind.PUNCT, "(")
            using = self._parse_identifier_list()
        return ast.Join(
            join_type=f"{'natural_' if natural else ''}{join_kind}",
            left=left,
            right=right,
            on=condition,
            using=using,
        )

    def parse_insert(self) -> ast.Node:
        start = self.expect_keyword("INSERT")
        top = self._parse_top() if self.at_keyword("TOP") else None
        self.accept_keyword("INTO") or self.accept_keyword("TABLE")
        target = self.parse_qualified_name()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        output, output_into = self._parse_output_clause()
        source: ast.Node
        if self.at_keyword("DEFAULT"):
            self.advance()
            self.expect_keyword("VALUES")
            source = ast.DefaultValues()
        elif self.at_keyword("VALUES"):
            source = self.parse_values()
        else:
            source = self.parse_query()
        return TSqlInsert(
            target=target,
            columns=columns,
            source=source,
            top=top,
            output=output,
            output_into=output_into,
            span=self._token_span(start),
        )

    def parse_update(self) -> ast.Node:
        start = self.expect_keyword("UPDATE")
        top = self._parse_top() if self.at_keyword("TOP") else None
        target = self.parse_qualified_name()
        alias = self._parse_optional_alias()
        self.expect_keyword("SET")
        assignments = self.parse_comma_list(self._parse_tsql_assignment)
        output, output_into = self._parse_output_clause()
        from_items = self._parse_from_list() if self.accept_keyword("FROM") else []
        target, alias = self._resolve_dml_alias(target, alias, from_items)
        where = self.parse_expr() if self.accept_keyword("WHERE") else None
        return TSqlUpdate(
            target=target,
            alias=alias,
            assignments=assignments,
            from_=from_items,
            where=where,
            top=top,
            output=output,
            output_into=output_into,
            span=self._token_span(start),
        )

    def parse_delete(self) -> ast.Node:
        start = self.expect_keyword("DELETE")
        top = self._parse_top() if self.at_keyword("TOP") else None
        if self.accept_keyword("FROM"):
            target = self.parse_qualified_name()
            alias = self._parse_optional_alias()
            from_items: list[ast.Node] = []
        else:
            target = self.parse_qualified_name()
            alias = self._parse_optional_alias()
            output, output_into = self._parse_output_clause()
            from_items = self._parse_from_list() if self.accept_keyword("FROM") else []
            target, alias = self._resolve_dml_alias(target, alias, from_items)
            where = self.parse_expr() if self.accept_keyword("WHERE") else None
            return TSqlDelete(
                target=target,
                alias=alias,
                using=from_items,
                where=where,
                top=top,
                output=output,
                output_into=output_into,
                span=self._token_span(start),
            )
        output, output_into = self._parse_output_clause()
        where = self.parse_expr() if self.accept_keyword("WHERE") else None
        return TSqlDelete(
            target=target,
            alias=alias,
            using=from_items,
            where=where,
            top=top,
            output=output,
            output_into=output_into,
            span=self._token_span(start),
        )

    def parse_merge(self) -> ast.Node:
        start = self.peek()
        self.expect_keyword("MERGE")
        top = self._parse_top() if self.at_keyword("TOP") else None
        self.accept_keyword("INTO")
        target = self.parse_qualified_name()
        alias = self._parse_optional_alias()
        self.expect_keyword("USING")
        source = self._parse_from_item()
        self.expect_keyword("ON")
        condition = self.parse_expr()
        clauses: list[ast.MergeClause] = []
        output: list[ast.SelectItem] = []
        output_into = None
        while self.accept_keyword("WHEN"):
            matched = bool(self.accept_keyword("MATCHED"))
            if not matched:
                self.expect_keyword("NOT")
                self.expect_keyword("MATCHED")
                if self.accept_keyword("BY"):
                    self.accept_keyword("SOURCE") or self.accept_keyword("TARGET")
            clause_condition = self.parse_expr() if self.accept_keyword("AND") else None
            self.expect_keyword("THEN")
            if self.accept_keyword("UPDATE"):
                self.expect_keyword("SET")
                action: ast.Node = ast.Update(
                    target=target, assignments=self.parse_comma_list(self._parse_tsql_assignment)
                )
            elif self.accept_keyword("DELETE"):
                action = ast.Delete(target=target)
            elif self.accept_keyword("INSERT"):
                columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
                action = ast.Insert(target=target, columns=columns, source=self.parse_values())
            else:
                raise ParseError("Expected MERGE action.", self.peek())
            clauses.append(ast.MergeClause(matched, clause_condition, action))
        if self.accept_keyword("OUTPUT"):
            output, output_into = self._parse_output_items()
        if not (self.at(TokenKind.TERMINATOR) and self.peek().text == ";"):
            raise ParseError("T-SQL MERGE requires a terminating semicolon.", self.peek())
        return TSqlMerge(
            target=target,
            alias=alias,
            source=source,
            on=condition,
            clauses=clauses,
            top=top,
            output=output,
            output_into=output_into,
            span=self._token_span(start),
        )

    def _parse_top(self) -> ast.Top:
        self.expect_keyword("TOP")
        paren = bool(self.accept(TokenKind.PUNCT, "("))
        count = self.parse_expr(70)
        if paren:
            self.expect(TokenKind.PUNCT, ")")
        percent = bool(self.accept_keyword("PERCENT"))
        ties = False
        if self.accept_keyword("WITH"):
            self.expect_keyword("TIES")
            ties = True
        return ast.Top(count, percent, ties)

    def _parse_output_clause(
        self,
    ) -> tuple[list[ast.SelectItem], ast.QualifiedName | None]:
        if not self.accept_keyword("OUTPUT"):
            return [], None
        return self._parse_output_items()

    def _parse_output_items(
        self,
    ) -> tuple[list[ast.SelectItem], ast.QualifiedName | None]:
        items = self.parse_comma_list(self._parse_select_item)
        output_into = None
        if self.accept_keyword("INTO"):
            output_into = self.parse_qualified_name()
            if self.accept(TokenKind.PUNCT, "("):
                self._parse_identifier_list()
        return items, output_into

    def _parse_tsql_assignment(self) -> ast.Assignment:
        target = self.parse_expr(70)
        if self.accept(TokenKind.OPERATOR, "="):
            return ast.Assignment(target, self.parse_expr())
        if self.at(TokenKind.OPERATOR, "+") or self.at(TokenKind.OPERATOR, "-"):
            operator = self.advance().text
            self.expect(TokenKind.OPERATOR, "=")
            value = self.parse_expr()
            return ast.Assignment(
                target,
                ast.DialectExpr(
                    name="compound_assignment", args={"operator": f"{operator}=", "expr": value}
                ),
            )
        raise ParseError("Expected assignment operator.", self.peek())

    def _parse_tsql_declare(self) -> ast.Node:
        start = self.expect_keyword("DECLARE")
        declarations: list[ast.Node] = []
        while True:
            if self.at(TokenKind.PARAM):
                var_token = self.advance()
                name = ast.Identifier(value=var_token.text, span=self._token_span(var_token))
            else:
                name = self.parse_identifier()
            if self.accept_keyword("CURSOR"):
                options: dict[str, Any] = {}
                while self.at_keyword(
                    "LOCAL",
                    "GLOBAL",
                    "FORWARD_ONLY",
                    "SCROLL",
                    "STATIC",
                    "KEYSET",
                    "DYNAMIC",
                    "FAST_FORWARD",
                    "READ_ONLY",
                ):
                    options.setdefault("cursor_options", []).append(self.advance().text.casefold())
                self.accept_keyword("FOR")
                declarations.append(
                    ast.CursorDecl(name=name, query=self.parse_query(), options=options)
                )
            elif self.accept_keyword("TABLE"):
                self.expect(TokenKind.PUNCT, "(")
                fields: list[ast.StructField] = []
                while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                    column = self._parse_column_def()
                    fields.append(ast.StructField(name=column.name, type=column.type))
                    if not self.accept(TokenKind.PUNCT, ","):
                        break
                self.expect(TokenKind.PUNCT, ")")
                declarations.append(
                    ast.Declare(name=name, type=ast.DataType(name="TABLE", children=fields))
                )
            else:
                self.accept_keyword("AS")
                data_type = self.parse_data_type()
                default = None
                if self.accept(TokenKind.OPERATOR, "=") or self.accept_keyword("DEFAULT"):
                    default = self.parse_expr()
                declarations.append(ast.Declare(name=name, type=data_type, default=default))
            if not self.accept(TokenKind.PUNCT, ","):
                break
        if len(declarations) == 1:
            return declarations[0]
        return ast.Block(declarations=declarations, span=self._token_span(start))

    def _parse_tsql_set(self) -> ast.SetStatement:
        start = self.expect_keyword("SET")
        if self.accept_keyword("TRANSACTION"):
            self.expect_keyword("ISOLATION")
            self.expect_keyword("LEVEL")
            value = " ".join(
                [self.advance().text, self.advance().text]
                if self.at_keyword("READ") and self.at_keyword("COMMITTED", 1)
                else [self.advance().text]
            )
            return ast.SetStatement(
                scope="option",
                items=[
                    ast.SetItem(
                        name=ast.ColumnRef(_identifier_name("TRANSACTION ISOLATION LEVEL")),
                        value=ast.Literal("string", value),
                    )
                ],
                span=self._token_span(start),
            )
        name = self.parse_expr(70)
        if self.at(TokenKind.OPERATOR, "="):
            self.advance()
            value = self.parse_expr()
        elif self.at(TokenKind.OPERATOR, "+") or self.at(TokenKind.OPERATOR, "-"):
            operator = self.advance().text
            self.expect(TokenKind.OPERATOR, "=")
            value = ast.DialectExpr(
                name="compound_assignment",
                args={"operator": f"{operator}=", "expr": self.parse_expr()},
            )
        else:
            value_token = self.advance()
            value = ast.Literal(
                "boolean" if value_token.text.upper() in {"ON", "OFF"} else "string",
                value_token.value,
                span=self._token_span(value_token),
            )
        return ast.SetStatement(
            scope="option" if isinstance(name, ast.ColumnRef) else None,
            items=[ast.SetItem(name, value)],
            span=self._token_span(start),
        )

    def parse_block(self) -> ast.Block:
        if self.at_keyword("BEGIN") and self.at_keyword("TRY", 1):
            return self._parse_try_catch()
        block = super().parse_block()
        if block.label and block.label.upper() in _TSQL_START_WORDS:
            self.index -= 1
            block.label = None
        return block

    def _parse_try_catch(self) -> ast.Block:
        start = self.expect_keyword("BEGIN")
        self.expect_keyword("TRY")
        try_statements = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.expect_keyword("TRY")
        self.expect_keyword("BEGIN")
        self.expect_keyword("CATCH")
        catch_statements = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.expect_keyword("CATCH")
        handler = ast.ExceptionHandler(conditions=["catch"], statements=catch_statements)
        return ast.Block(
            statements=try_statements,
            exception_handlers=[handler],
            span=self._token_span(start),
        )

    def parse_if(self) -> ast.If:
        start = self.expect_keyword("IF")
        condition = self.parse_expr()
        body = self._parse_tsql_body()
        branches = [(condition, body)]
        else_body = self._parse_tsql_body() if self.accept_keyword("ELSE") else []
        return ast.If(branches=branches, else_=else_body, span=self._token_span(start))

    def parse_while(self) -> ast.While:
        start = self.expect_keyword("WHILE")
        condition = self.parse_expr()
        body = self._parse_tsql_body()
        return ast.While(condition=condition, statements=body, span=self._token_span(start))

    def _parse_tsql_body(self) -> list[ast.Node]:
        if self.at_keyword("BEGIN"):
            return [self.parse_block()]
        return [self.parse_statement()]

    def parse_fetch(self) -> ast.Fetch:
        start = self.expect_keyword("FETCH")
        self.accept_keyword("NEXT") or self.accept_keyword("FIRST")
        self.accept_keyword("PRIOR") or self.accept_keyword("LAST")
        self.accept_keyword("FROM")
        name = self.parse_identifier()
        into: list[ast.Identifier] = []
        if self.accept_keyword("INTO"):
            while True:
                if self.at(TokenKind.PARAM):
                    token = self.advance()
                    into.append(ast.Identifier(value=token.text, span=self._token_span(token)))
                else:
                    into.append(self.parse_identifier())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
        return ast.Fetch(name=name, into=into, span=self._token_span(start))

    def _parse_exec(self) -> ast.Node:
        start = self.advance()
        return_variable = None
        if self.at(TokenKind.PARAM) and self.peek(1).text == "=":
            return_variable = self.advance().text
            self.advance()
        if self.accept(TokenKind.PUNCT, "("):
            sql_expr = self.parse_expr()
            self.expect(TokenKind.PUNCT, ")")
            parsed = self._parse_embedded_sql(sql_expr)
            return ast.DynamicSql(sql_expr=sql_expr, parsed=parsed, span=self._token_span(start))
        if self.at(TokenKind.PARAM) and not return_variable:
            sql_expr = self.parse_primary_ext()
            return ast.DynamicSql(sql_expr=sql_expr, span=self._token_span(start))
        name = self.parse_qualified_name()
        if name.parts[-1].value.casefold() == "sp_executesql":
            first = self.parse_expr(70) if not self._statement_end() else None
            using: list[ast.Node] = []
            while self.accept(TokenKind.PUNCT, ","):
                if self.at(TokenKind.PARAM) and self.peek(1).text == "=":
                    arg_name = self.advance()
                    self.advance()
                    using.append(
                        ast.NamedArg(ast.Identifier(value=arg_name.text), self.parse_expr())
                    )
                else:
                    using.append(self.parse_expr())
            parsed = self._parse_embedded_sql(first) if first is not None else []
            return ast.DynamicSql(
                sql_expr=first,
                using=using,
                parsed=parsed,
                span=self._token_span(start),
            )
        args: list[ast.Node] = []
        while not self._statement_end() and not self.at_keyword("AT"):
            if self.accept(TokenKind.PUNCT, ","):
                continue
            if self.at(TokenKind.PARAM) and self.peek(1).text == "=":
                arg_name = self.advance()
                self.advance()
                value = self.parse_expr(31)
                if self.accept_keyword("OUTPUT") or self.accept_keyword("OUT"):
                    value = ast.DialectExpr(name="output_arg", args={"expr": value})
                args.append(ast.NamedArg(ast.Identifier(value=arg_name.text), value))
            else:
                value = self.parse_expr(31)
                if self.accept_keyword("OUTPUT") or self.accept_keyword("OUT"):
                    value = ast.DialectExpr(name="output_arg", args={"expr": value})
                args.append(value)
            if (
                not self.at(TokenKind.PUNCT, ",")
                and not self.at(TokenKind.PARAM)
                and not self.at(TokenKind.STRING)
            ):
                break
        if self.accept_keyword("AT"):
            server = self.parse_qualified_name()
            return ast.DynamicSql(
                sql_expr=ast.Call(name=name, args=args),
                using=[ast.ColumnRef(server)],
                parsed=[],
                span=self._token_span(start),
            )
        call = ast.Call(name=name, args=args)
        if return_variable:
            call.args.insert(
                0, ast.NamedArg(ast.Identifier(value="return"), ast.Variable(return_variable))
            )
        return call

    def _parse_throw(self) -> ast.Raise:
        start = self.expect_keyword("THROW")
        args: list[ast.Node] = []
        if not self._statement_end():
            args.append(self.parse_expr(31))
            while self.accept(TokenKind.PUNCT, ","):
                args.append(self.parse_expr(31))
        return ast.Raise(
            expr=ast.DialectExpr(name="throw", args={"args": args}),
            span=self._token_span(start),
        )

    def parse_create(self) -> ast.Node:
        start_index = self.index
        start = self.expect_keyword("CREATE")
        alter = False
        or_alter = False
        replace = False
        if self.accept_keyword("OR"):
            alter = bool(self.accept_keyword("ALTER"))
            or_alter = alter
            if not alter:
                replace = bool(self.accept_keyword("REPLACE"))
        if self.accept_keyword("UNIQUE"):
            unique = True
        else:
            unique = False
        clustered = None
        if self.accept_keyword("CLUSTERED"):
            clustered = "clustered"
        elif self.accept_keyword("NONCLUSTERED"):
            clustered = "nonclustered"
        columnstore = bool(self.accept_keyword("COLUMNSTORE"))
        if self.accept_keyword("INDEX"):
            return self._parse_tsql_index(start, unique, clustered, columnstore)
        if clustered or columnstore:
            raise ParseError("Expected INDEX after index options.", self.peek())
        if self.accept_keyword("TABLE"):
            return self._parse_create_table(start, False, False, replace)
        if self.accept_keyword("VIEW"):
            return self._parse_tsql_view(start, replace)
        if self.accept_keyword("MATERIALIZED"):
            self.expect_keyword("VIEW")
            return self._parse_tsql_view(start, replace, materialized=True)
        if self.accept_keyword("PROCEDURE") or self.accept_keyword("PROC"):
            routine = self._parse_tsql_routine(start, "procedure", alter=alter)
            if or_alter:
                routine.options["or_alter"] = True
            return routine
        if self.accept_keyword("FUNCTION"):
            routine = self._parse_tsql_routine(start, "function", alter=alter)
            if or_alter:
                routine.options["or_alter"] = True
            return routine
        if self.accept_keyword("TRIGGER"):
            routine = self._parse_tsql_routine(start, "trigger", alter=alter)
            if or_alter:
                routine.options["or_alter"] = True
            return routine
        if self.accept_keyword("LOGIN"):
            return self._parse_login(start)
        if self.at_keyword("USER", "ROLE", "SCHEMA", "SEQUENCE", "SYNONYM", "DATABASE", "TYPE"):
            keyword = self.advance().text.casefold()
            if keyword == "type":
                return self._parse_create_type(start)
            self.index = start_index
            return super().parse_create()
        if alter or replace:
            raise ParseError("Unsupported CREATE OR ALTER statement.", self.peek())
        self.index = start_index
        return super().parse_create()

    def _parse_create_table(
        self, start: Token, temporary: bool, external: bool, replace: bool
    ) -> ast.CreateTable:
        if_not_exists = False
        if self.accept_keyword("IF"):
            self.expect_keyword("NOT")
            self.expect_keyword("EXISTS")
            if_not_exists = True
        name = self.parse_qualified_name()
        temporary = temporary or (bool(name.parts) and name.parts[0].value.startswith("#"))
        columns: list[ast.ColumnDef] = []
        constraints: list[ast.TableConstraint] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                if self._is_constraint_start():
                    constraints.append(self._parse_table_constraint())
                else:
                    columns.append(self._parse_column_def())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        options: dict[str, Any] = {}
        if self.accept_keyword("ON"):
            options["filegroup"] = self.parse_qualified_name()
        if self.accept_keyword("TEXTIMAGE_ON"):
            options["textimage_on"] = self.parse_qualified_name()
        if self.accept_keyword("WITH"):
            options.update(self._parse_tsql_option_list())
        as_query = self.parse_query() if self.accept_keyword("AS") else None
        return ast.CreateTable(
            name=name,
            columns=columns,
            constraints=constraints,
            as_query=as_query,
            temporary=temporary,
            external=external,
            replace=replace,
            if_not_exists=if_not_exists,
            options=options,
            span=self._token_span(start),
        )

    def _parse_column_def(self) -> ast.ColumnDef:
        name = self.parse_identifier()
        if self.accept_keyword("AS"):
            self.accept(TokenKind.PUNCT, "(")
            generated = self.parse_expr()
            self.accept(TokenKind.PUNCT, ")")
            self.accept_keyword("PERSISTED")
            return ast.ColumnDef(
                name=name,
                type=ast.DataType(name="computed"),
                generated=generated,
            )
        data_type = self.parse_data_type()
        constraints: list[ast.ColumnConstraint] = []
        default = None
        identity = None
        while (
            not self.at(TokenKind.PUNCT, ",")
            and not self.at(TokenKind.PUNCT, ")")
            and not self.at(TokenKind.EOF)
        ):
            if self.accept_keyword("IDENTITY"):
                start = self.tokens[self.index - 1]
                values: list[ast.Node] = []
                if self.accept(TokenKind.PUNCT, "("):
                    values = self.parse_comma_list(self.parse_expr)
                    self.expect(TokenKind.PUNCT, ")")
                identity = ast.DialectExpr(
                    name="identity", args={"args": values}, span=self._token_span(start)
                )
            elif self.accept_keyword("NOT"):
                self.expect_keyword("NULL")
                constraints.append(ast.ColumnConstraint("not_null"))
            elif self.accept_keyword("NULL"):
                constraints.append(ast.ColumnConstraint("null"))
            elif self.accept_keyword("PRIMARY"):
                self.expect_keyword("KEY")
                self.accept_keyword("CLUSTERED") or self.accept_keyword("NONCLUSTERED")
                constraints.append(ast.ColumnConstraint("primary_key"))
            elif self.accept_keyword("UNIQUE"):
                self.accept_keyword("CLUSTERED") or self.accept_keyword("NONCLUSTERED")
                constraints.append(ast.ColumnConstraint("unique"))
            elif self.accept_keyword("DEFAULT"):
                if self.accept(TokenKind.PUNCT, "("):
                    default = self.parse_expr()
                    self.expect(TokenKind.PUNCT, ")")
                else:
                    default = self.parse_expr(31)
            elif self.accept_keyword("REFERENCES"):
                table = self.parse_qualified_name()
                cols = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
                constraints.append(
                    ast.ColumnConstraint("foreign_key", ref_table=table, ref_columns=cols)
                )
            elif self.accept_keyword("CONSTRAINT"):
                constraint_name = self.parse_identifier()
                if self.accept_keyword("DEFAULT"):
                    self.accept_keyword("FOR")
                    self.parse_identifier()
                    self.expect(TokenKind.PUNCT, "(")
                    default = self.parse_expr()
                    self.expect(TokenKind.PUNCT, ")")
                    constraints.append(
                        ast.ColumnConstraint("default", name=constraint_name, expr=default)
                    )
                elif self.accept_keyword("REFERENCES"):
                    table = self.parse_qualified_name()
                    cols = (
                        self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
                    )
                    constraints.append(
                        ast.ColumnConstraint(
                            "foreign_key", name=constraint_name, ref_table=table, ref_columns=cols
                        )
                    )
                else:
                    constraints.append(ast.ColumnConstraint("named", name=constraint_name))
            elif self.accept_keyword("CHECK"):
                self.expect(TokenKind.PUNCT, "(")
                expr = self.parse_expr()
                self.expect(TokenKind.PUNCT, ")")
                constraints.append(ast.ColumnConstraint("check", expr=expr))
            elif self.accept_keyword("COLLATE"):
                collation = self.parse_qualified_name()
                constraints.append(ast.ColumnConstraint("collation", options={"name": collation}))
            else:
                break
        return ast.ColumnDef(
            name=name,
            type=data_type,
            constraints=constraints,
            default=default,
            identity=identity,
        )

    def _parse_table_constraint(self) -> ast.TableConstraint:
        constraint_name = None
        if self.accept_keyword("CONSTRAINT"):
            constraint_name = self.parse_identifier()
        if self.accept_keyword("PRIMARY"):
            self.expect_keyword("KEY")
            clustered = (
                self.advance().text.casefold()
                if self.at_keyword("CLUSTERED", "NONCLUSTERED")
                else None
            )
            columns = self._parse_identifier_list_in_parens()
            return ast.TableConstraint(
                "primary_key",
                name=constraint_name,
                columns=columns,
                options={"index_type": clustered},
            )
        if self.accept_keyword("UNIQUE"):
            clustered = (
                self.advance().text.casefold()
                if self.at_keyword("CLUSTERED", "NONCLUSTERED")
                else None
            )
            columns = self._parse_identifier_list_in_parens()
            return ast.TableConstraint(
                "unique", name=constraint_name, columns=columns, options={"index_type": clustered}
            )
        if self.accept_keyword("FOREIGN"):
            self.expect_keyword("KEY")
            columns = self._parse_identifier_list_in_parens()
            self.expect_keyword("REFERENCES")
            target = self.parse_qualified_name()
            ref_columns = (
                self._parse_identifier_list_in_parens() if self.at(TokenKind.PUNCT, "(") else []
            )
            return ast.TableConstraint(
                "foreign_key",
                name=constraint_name,
                columns=columns,
                ref_table=target,
                ref_columns=ref_columns,
            )
        return super()._parse_table_constraint()

    def _parse_tsql_option_list(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                key = self.advance().text.casefold()
                value: ast.Node | str = "on"
                if self.accept(TokenKind.OPERATOR, "="):
                    value = self._parse_option_value()
                options[key] = value
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        return options

    def _parse_option_value(self) -> ast.Node:
        token = self.peek()
        if token.kind in {TokenKind.STRING, TokenKind.NATIONAL_STRING}:
            self.advance()
            return ast.Literal("string", token.value, span=self._token_span(token))
        if token.kind is TokenKind.NUMBER:
            self.advance()
            return ast.Literal("number", token.value, span=self._token_span(token))
        if token.kind is TokenKind.PARAM:
            return self.parse_primary_ext()
        return ast.ColumnRef(_identifier_name(self.parse_identifier().value))

    def _parse_tsql_view(
        self, start: Token, replace: bool, materialized: bool = False
    ) -> ast.CreateView:
        name = self.parse_qualified_name()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        options: dict[str, Any] = {}
        if self.accept_keyword("WITH"):
            while self.at_keyword("SCHEMABINDING", "VIEW_METADATA", "ENCRYPTION"):
                options[self.advance().text.casefold()] = True
                self.accept(TokenKind.PUNCT, ",")
        self.expect_keyword("AS")
        query = self.parse_query()
        if self.accept_keyword("WITH"):
            self.accept_keyword("CHECK")
            self.accept_keyword("OPTION")
            options["check_option"] = True
        return ast.CreateView(
            name=name,
            columns=columns,
            query=query,
            materialized=materialized,
            replace=replace,
            options=options,
            span=self._token_span(start),
        )

    def _parse_tsql_index(
        self, start: Token, unique: bool, clustered: str | None, columnstore: bool
    ) -> ast.CreateIndex:
        name = self.parse_qualified_name()
        self.expect_keyword("ON")
        table = self.parse_qualified_name()
        columns: list[ast.Node] = []
        if self.accept(TokenKind.PUNCT, "("):
            columns = self.parse_comma_list(self.parse_expr)
            self.expect(TokenKind.PUNCT, ")")
        options: dict[str, Any] = {}
        if self.accept_keyword("INCLUDE"):
            self.expect(TokenKind.PUNCT, "(")
            options["include"] = self._parse_identifier_list()
        if self.accept_keyword("WHERE"):
            options["where"] = self.parse_expr()
        if self.accept_keyword("WITH"):
            options.update(self._parse_tsql_option_list())
        if self.accept_keyword("ON"):
            options["filegroup"] = self.parse_qualified_name()
        options.update(
            {
                key: value
                for key, value in {
                    "clustered": clustered,
                    "columnstore": columnstore or None,
                }.items()
                if value
            }
        )
        return ast.CreateIndex(
            name=name,
            table=table,
            columns=columns,
            unique=unique,
            options=options,
            span=self._token_span(start),
        )

    def _parse_tsql_routine(
        self, start: Token, routine_kind: str, *, alter: bool = False
    ) -> ast.CreateRoutine:
        name = self.parse_qualified_name()
        params: list[ast.RoutineParam] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                params.append(self._parse_tsql_param())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        else:
            while self.at(TokenKind.PARAM):
                params.append(self._parse_tsql_param())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
        options: dict[str, Any] = {"alter": alter} if alter else {}
        if self.accept_keyword("WITH"):
            opts = []
            while not self.at_keyword("AS", "RETURNS") and not self.at(TokenKind.EOF):
                opts.append(self.advance().text)
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            options["with"] = " ".join(opts)
        trigger_on = None
        if routine_kind == "trigger":
            if self.accept_keyword("ON"):
                trigger_on = self.parse_qualified_name()
            timing = "after"
            if self.accept_keyword("INSTEAD"):
                self.expect_keyword("OF")
                timing = "instead_of"
            elif self.accept_keyword("AFTER"):
                timing = "after"
            elif self.accept_keyword("FOR"):
                timing = "after"
            events: list[dict[str, Any]] = []
            while not self.at_keyword("ON", "AS") and not self.at(TokenKind.EOF):
                if self.accept_keyword("INSERT", "UPDATE", "DELETE"):
                    event = self.tokens[self.index - 1].text.casefold()
                    columns = (
                        self._parse_identifier_list_in_parens()
                        if event == "update" and self.at(TokenKind.PUNCT, "(")
                        else []
                    )
                    events.append({"event": event, "columns": columns})
                elif self.accept(TokenKind.PUNCT, ","):
                    continue
                else:
                    self.advance()
            if trigger_on is None and self.accept_keyword("ON"):
                trigger_on = self.parse_qualified_name()
            options.update({"timing": timing, "events": events, "trigger_on": trigger_on})
            if self.accept_keyword("WITH"):
                self.accept_keyword("APPEND")
                options["append"] = True
        returns = None
        if self.accept_keyword("RETURNS"):
            if self.accept_keyword("TABLE"):
                returns = ast.DataType(name="TABLE")
                options["function_form"] = "inline_table"
            elif self.at(TokenKind.PARAM):
                var = self.advance().text
                self.expect_keyword("TABLE")
                columns = []
                if self.accept(TokenKind.PUNCT, "("):
                    while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                        columns.append(self._parse_column_def())
                        if not self.accept(TokenKind.PUNCT, ","):
                            break
                    self.expect(TokenKind.PUNCT, ")")
                returns = ast.DataType(name="TABLE", children=[])
                options.update(
                    {
                        "function_form": "multi_statement",
                        "returns_variable": var,
                        "returns_columns": columns,
                    }
                )
            else:
                returns = self.parse_data_type()
        if self.accept_keyword("WITH"):
            if self.accept_keyword("SCHEMABINDING"):
                options["schemabinding"] = True
            else:
                self.index -= 1
        self.expect_keyword("AS")
        prior_trigger = self._inside_trigger
        self._inside_trigger = routine_kind == "trigger"
        try:
            if self.at_keyword("BEGIN"):
                body: ast.Node = self.parse_block()
            elif routine_kind == "function" and self.accept_keyword("RETURN"):
                if self.accept(TokenKind.PUNCT, "("):
                    body = self.parse_query()
                    self.expect(TokenKind.PUNCT, ")")
                    options.setdefault("function_form", "inline_table")
                else:
                    body = ast.Return(self.parse_expr())
            else:
                statements: list[ast.Node] = []
                while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR, "GO"):
                    if self.at(TokenKind.TERMINATOR):
                        self.advance()
                        continue
                    before = self.index
                    statements.append(self.parse_statement())
                    if self.index == before:
                        raise ParseError("Routine parser made no progress.", self.peek())
                    self.accept(TokenKind.TERMINATOR)
                body = ast.Block(statements=statements)
        finally:
            self._inside_trigger = prior_trigger
        return ast.CreateRoutine(
            routine_kind=routine_kind,
            name=name,
            params=params,
            returns=returns,
            body=body,
            options=options,
            span=self._token_span(start),
        )

    def _parse_tsql_param(self) -> ast.RoutineParam:
        token = self.advance()
        name = ast.Identifier(value=token.text, span=self._token_span(token))
        self.accept_keyword("AS")
        data_type = self.parse_data_type()
        default = None
        if self.accept(TokenKind.OPERATOR, "="):
            default = self.parse_expr(31)
        mode = None
        if self.accept_keyword("OUTPUT") or self.accept_keyword("OUT"):
            mode = "out"
        elif self.accept_keyword("READONLY"):
            mode = "readonly"
        return ast.RoutineParam(name=name, mode=mode, type=data_type, default=default)

    def _parse_create_type(self, start: Token) -> ast.Node:
        name = self.parse_qualified_name()
        if self.accept_keyword("FROM"):
            data_type = self.parse_data_type()
            options = {"base_type": data_type}
        elif self.accept_keyword("AS") and self.accept_keyword("TABLE"):
            columns: list[ast.ColumnDef] = []
            if self.accept(TokenKind.PUNCT, "("):
                while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                    columns.append(self._parse_column_def())
                    if not self.accept(TokenKind.PUNCT, ","):
                        break
                self.expect(TokenKind.PUNCT, ")")
            options = {"table_type": True, "columns": columns}
        else:
            raise ParseError("Expected FROM or AS TABLE in CREATE TYPE.", self.peek())
        return ast.CreateRoutine(
            routine_kind="type", name=name, options=options, span=self._token_span(start)
        )

    def _parse_login(self, start: Token) -> ast.CreatePrincipal:
        name = self.parse_qualified_name()
        options: dict[str, Any] = {}
        if self.accept_keyword("WITH"):
            while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR):
                key = self.advance().text.casefold()
                self.accept(TokenKind.OPERATOR, "=")
                if key == "password":
                    if self.at(TokenKind.STRING):
                        self.advance()
                    options["password"] = "***"
                elif key == "default_database":
                    options[key] = self.parse_qualified_name()
                elif key == "sid" and self.at(TokenKind.HEX_STRING):
                    options[key] = ast.Literal("hex", self.advance().value)
                else:
                    options[key] = self._parse_option_value()
                if not self.accept(TokenKind.PUNCT, ","):
                    break
        elif self.accept_keyword("FROM"):
            options["from"] = self.advance().text.casefold()
        return ast.CreatePrincipal(
            principal_kind="login", name=name, options=options, span=self._token_span(start)
        )

    def _parse_alter_role(self) -> ast.Alter:
        start = self.expect_keyword("ALTER")
        self.expect_keyword("ROLE")
        name = self.parse_qualified_name()
        action = self.advance().text.casefold()
        if action in {"add", "drop"}:
            self.expect_keyword("MEMBER")
            member = self.parse_qualified_name()
            node = ast.DialectStatement(name=f"{action}_member", args={"member": member})
        else:
            node = ast.DialectStatement(name="role_action", args={"action": action})
        return ast.Alter("role", name, [node], span=self._token_span(start))

    def _parse_alter_routine(self) -> ast.Node:
        start = self.expect_keyword("ALTER")
        kind = self.advance().text.casefold()
        if kind == "proc":
            kind = "procedure"
        return self._parse_tsql_routine(start, kind, alter=True)

    def _parse_deny(self) -> ast.DialectStatement:
        start = self.expect_keyword("DENY")
        privileges = self.parse_comma_list(lambda: self.advance().text)
        self.expect_keyword("ON")
        object_kind = "object"
        if self.accept_keyword("OBJECT", "SCHEMA"):
            object_kind = self.tokens[self.index - 1].text.casefold()
            self.accept(TokenKind.OPERATOR, "::")
        object_name = self.parse_qualified_name()
        self.expect_keyword("TO")
        grantees = self.parse_comma_list(self.parse_qualified_name)
        return ast.DialectStatement(
            name="deny",
            args={"privileges": privileges, "object_kind": object_kind, "grantees": grantees},
            references=[ast.RefHint(object_name, "grant_on")],
            span=self._token_span(start),
        )

    def parse_grant(self) -> ast.Node:
        start = self.expect_keyword("GRANT")
        privileges = self.parse_comma_list(lambda: self.advance().text)
        object_kind = "object"
        object_name = _identifier_name("")
        if self.accept_keyword("ON"):
            if self.accept_keyword("OBJECT", "SCHEMA"):
                object_kind = self.tokens[self.index - 1].text.casefold()
                self.accept(TokenKind.OPERATOR, "::")
            object_name = self.parse_qualified_name()
        self.expect_keyword("TO")
        grantees = self.parse_comma_list(self.parse_qualified_name)
        grant_option = False
        if self.accept_keyword("WITH"):
            self.expect_keyword("GRANT")
            self.expect_keyword("OPTION")
            grant_option = True
        return ast.Grant(
            privileges,
            object_kind,
            object_name,
            grantees,
            grant_option,
            span=self._token_span(start),
        )

    def parse_revoke(self) -> ast.Revoke:
        start = self.expect_keyword("REVOKE")
        grant_option = False
        if self.accept_keyword("GRANT"):
            self.expect_keyword("OPTION")
            self.expect_keyword("FOR")
            grant_option = True
        privileges = self.parse_comma_list(lambda: self.advance().text)
        self.expect_keyword("ON")
        object_kind = "object"
        if self.accept_keyword("OBJECT", "SCHEMA"):
            object_kind = self.tokens[self.index - 1].text.casefold()
            self.accept(TokenKind.OPERATOR, "::")
        object_name = self.parse_qualified_name()
        self.expect_keyword("FROM")
        grantees = self.parse_comma_list(self.parse_qualified_name)
        return ast.Revoke(
            privileges,
            object_kind,
            object_name,
            grantees,
            grant_option,
            span=self._token_span(start),
        )

    def _resolve_dml_alias(
        self, target: ast.QualifiedName, alias: ast.Identifier | None, from_items: list[ast.Node]
    ) -> tuple[ast.QualifiedName, ast.Identifier | None]:
        if len(target.parts) != 1:
            return target, alias
        target_name = target.parts[0].value.casefold()
        for item in from_items:
            table = self._find_alias(item, target_name)
            if table is not None:
                return table.name, table.alias
        return target, alias

    def _find_alias(self, item: ast.Node, alias_name: str) -> ast.TableRef | None:
        if (
            isinstance(item, ast.TableRef)
            and item.alias
            and item.alias.value.casefold() == alias_name
        ):
            return item
        if isinstance(item, ast.Join):
            return self._find_alias(item.left, alias_name) or self._find_alias(
                item.right, alias_name
            )
        return None


class Parser(TSqlParserBase):
    pass


__all__ = ["Parser", "TSqlParserBase"]
