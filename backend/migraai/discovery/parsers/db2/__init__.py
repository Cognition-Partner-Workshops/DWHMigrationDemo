from __future__ import annotations

import re
from typing import Any

from migraai.discovery import ast
from migraai.discovery.lexer import Comment, Lexer, LexerConfig, Token, TokenKind
from migraai.discovery.parser_base import ParserBase
from migraai.discovery.parsers.teradata.psm import PsmMixin


class Db2Lexer(Lexer):
    """Lexer with Db2's source-level SET TERMINATOR directive."""

    def __init__(self, text: str, config: LexerConfig | None = None) -> None:
        self.active_terminator: str | None = None
        self.terminator_contexts: list[str | None] = []
        super().__init__(text, config)

    def _emit(self, kind: TokenKind, text: str, value: str) -> None:
        super()._emit(kind, text, value)
        self.terminator_contexts.append(self.active_terminator)

    def _scan_line_comment(self) -> None:
        end = self.text.find("\n", self._offset)
        if end < 0:
            end = len(self.text)
        raw = self.text[self._offset : end]
        match = re.fullmatch(r"--#SET\s+TERMINATOR\s+(\S)\s*", raw, flags=re.IGNORECASE)
        if match:
            line, col = self._position()
            self.comments.append(Comment(raw, self._span(line, col, raw)))
            terminator = match.group(1)
            self.active_terminator = None if terminator == ";" else terminator
            self._advance(raw)
            return
        super()._scan_line_comment()

    def _scan_operator(self) -> bool:
        if self.active_terminator:
            remaining = self.text[self._offset :]
            if remaining.startswith(self.active_terminator):
                self._emit(TokenKind.TERMINATOR, self.active_terminator, self.active_terminator)
                self._advance(self.active_terminator)
                return True
        return super()._scan_operator()


class Parser(PsmMixin, ParserBase):
    case_rule = "insensitive"
    builtin_functions = ParserBase.ansi_builtins | frozenset(
        {
            "BIGINT",
            "CHAR",
            "CHARACTER",
            "CURRENT_DATE",
            "CURRENT_TIMESTAMP",
            "DAYS",
            "DECFLOAT",
            "DIGITS",
            "GRAPHIC",
            "INT",
            "INTEGER",
            "JULIAN_DAY",
            "NEXTVAL",
            "PREVVAL",
            "TIMESTAMP",
            "VARCHAR",
        }
    )
    lexer_config = LexerConfig(
        ident_quotes={'"': '"'},
        statement_terminators=(";",),
        dot_command_prefixes=(),
        param_styles=frozenset({"question", "colon"}),
    )

    def __init__(self, text: str | None = None, rel_path: str = "", terminator: str = ";") -> None:
        super().__init__(text, rel_path, lexer_config=self.lexer_config)
        self.default_schema: str | None = None
        self._active_terminators: list[str | None] = []
        self._query_depth = 0
        self._requested_terminator = terminator

    def parse(self, text: str | None = None, rel_path: str | None = None) -> ast.ParseResult:
        if text is not None:
            self.text = text
        if rel_path is not None:
            self.rel_path = rel_path
        lexer = Db2Lexer(self.text, self.lexer_config)
        self.tokens = lexer.tokenize()
        self._active_terminators = lexer.terminator_contexts
        lexer_issues = list(lexer.issues)
        result = super().parse(None, rel_path)
        result.issues[:0] = lexer_issues
        self.issues = result.issues
        self.default_schema = None
        for statement in result.statements:
            if isinstance(statement, ast.Use):
                self.default_schema = ".".join(part.value for part in statement.database.parts)
        return result

    def _recover(self, start: int) -> None:
        terminator = (
            self._active_terminators[start] if start < len(self._active_terminators) else None
        )
        if not terminator:
            return super()._recover(start)
        if self.index <= start:
            self.index = min(start + 1, len(self.tokens) - 1)
        depth = 0
        while self.index < len(self.tokens) - 1:
            token = self.tokens[self.index]
            if token.kind is TokenKind.PUNCT and token.text == "(":
                depth += 1
            elif token.kind is TokenKind.PUNCT and token.text == ")":
                depth = max(0, depth - 1)
            elif token.kind is TokenKind.TERMINATOR and token.text == terminator and depth == 0:
                self.index += 1
                return
            self.index += 1

    def parse_statement_ext(self) -> ast.Node | None:
        if self.at_keyword("RETURN") and self.at_keyword("SELECT", "WITH", 1):
            token = self.advance()
            return ast.Return(self.parse_query(), span=self._token_span(token))
        if self.at_keyword("CONNECT", "TERMINATE"):
            return self._parse_clp_command()
        if self.at_keyword("SET"):
            if self.at_keyword("SCHEMA", 1) or (
                self.at_keyword("CURRENT", 1) and self.at_keyword("SCHEMA", 2)
            ):
                return self._parse_set_schema()
            if self.at_keyword("PATH", 1) or (
                self.at_keyword("CURRENT", 1) and self.at_keyword("DEGREE", 2)
            ):
                return self._parse_set_command()
        if self.at_keyword("VALUES") and not (
            self.peek(1).kind is TokenKind.PUNCT and self.peek(1).text == "("
        ):
            return self._parse_values_into()
        if self.at_keyword("REFRESH") and self.at_keyword("TABLE", 1):
            self.advance()
            self.advance()
            name = self.parse_qualified_name()
            return ast.DialectStatement(
                "refresh_table",
                {"name": name},
                [ast.RefHint(name, "writes")],
            )
        if self.at_keyword("ALTER") and self.at_keyword("TABLE", 1):
            return self._parse_alter_db2()
        if self.at_keyword("CREATE"):
            return self._parse_create_db2()
        if self.at_keyword("GRANT"):
            return self._parse_grant_db2()
        if self.at_keyword("REVOKE"):
            return self._parse_revoke_db2()
        if self.at_keyword("ALLOCATE"):
            start = self.advance()
            cursor = self.parse_identifier()
            self.expect_keyword("CURSOR")
            self.expect_keyword("FOR")
            self.expect_keyword("RESULT")
            self.expect_keyword("SET")
            result_set = self.parse_identifier()
            return ast.DialectStatement(
                "allocate_result_set_cursor",
                {"cursor": cursor, "result_set": result_set},
                span=self._token_span(start),
            )
        if self.at_keyword("EXECUTE") and self.at_keyword("IMMEDIATE", 1):
            return super().parse_statement_ext()
        if self.at_keyword("EXECUTE") and self._is_name():
            token = self.advance()
            name = self.parse_qualified_name()
            using = []
            if self.accept_keyword("USING"):
                using = self.parse_comma_list(self.parse_expr)
            return ast.DynamicSql(
                sql_expr=ast.Call(name, []),
                using=using,
                parsed=[ast.Call(name, [])],
                span=self._token_span(token),
            )
        return super().parse_statement_ext()

    def _parse_clp_command(self) -> ast.ClientCommand:
        start = self.advance()
        words = [start.text.upper()]
        if start.text.casefold() == "connect":
            if self.accept_keyword("RESET"):
                words.append("RESET")
            elif self.accept_keyword("TO"):
                words.append("TO")
                words.append(self.parse_qualified_name().to_dict()["parts"][-1]["value"])
                if self.accept_keyword("USER"):
                    words.extend(["USER", self.parse_identifier().value])
                if self.accept_keyword("USING"):
                    while not self.at(TokenKind.TERMINATOR) and not self.at(TokenKind.EOF):
                        self.advance()
        return ast.ClientCommand(" ".join(words), span=self._token_span(start))

    def _parse_set_schema(self) -> ast.Use:
        self.expect_keyword("SET")
        self.accept_keyword("CURRENT")
        self.expect_keyword("SCHEMA")
        self.accept(TokenKind.OPERATOR, "=")
        target = self.parse_qualified_name()
        return ast.Use(database=target)

    def _parse_set_command(self) -> ast.SetStatement:
        self.expect_keyword("SET")
        scope = None
        if self.accept_keyword("CURRENT"):
            scope = "current"
        name = self.parse_identifier()
        if name.value.casefold() == "degree":
            self.expect(TokenKind.OPERATOR, "=")
        else:
            self.accept(TokenKind.OPERATOR, "=")
        values = self.parse_comma_list(self.parse_expr)
        return ast.SetStatement(
            scope,
            [ast.SetItem(ast.ColumnRef(ast.QualifiedName([name])), value) for value in values],
        )

    def _parse_values_into(self) -> ast.DialectStatement:
        start = self.expect_keyword("VALUES")
        values = self.parse_comma_list(self.parse_expr)
        self.expect_keyword("INTO")
        targets = self.parse_comma_list(self._parse_target_identifier)
        return ast.DialectStatement(
            "values_into", {"values": values, "targets": targets}, span=self._token_span(start)
        )

    def _parse_target_identifier(self) -> ast.Identifier:
        if self.at(TokenKind.PARAM):
            token = self.advance()
            return ast.Identifier(value=token.text)
        return self.parse_identifier()

    def _parse_create_db2(self) -> ast.Node | None:
        start_index = self.index
        start = self.advance()
        replace = False
        if self.accept_keyword("OR"):
            self.expect_keyword("REPLACE")
            replace = True
        kind = self.peek().text.casefold()
        if kind == "public" and self.at_keyword("alias", 1):
            self.advance()
            kind = "alias"
        if kind in {"temporary", "global"} and self.at_keyword("TABLE", 1):
            self.advance()
            kind = "table"
        if kind == "table":
            self.advance()
            return self._parse_table_db2(start, replace)
        if kind == "view":
            self.advance()
            return self._parse_create_view(start, replace)
        if kind == "materialized" and self.at_keyword("VIEW", 1):
            self.advance()
            self.advance()
            return self._parse_create_view(start, replace, materialized=True)
        if kind in {"procedure", "function"}:
            self.advance()
            return self._parse_routine_db2(start, kind)
        if kind in {"permission", "mask"}:
            self.advance()
            return self._parse_rcac(start, kind)
        if kind in {"alias", "synonym"}:
            self.advance()
            return self._parse_alias(start, kind)
        if kind == "sequence":
            self.advance()
            return self._parse_sequence(start)
        if kind == "index":
            self.advance()
            return self._parse_index_db2(start)
        if kind == "user":
            self.advance()
            return self._parse_user(start)
        self.index = start_index
        return ParserBase.parse_create(self)

    def _parse_table_db2(self, start: Token, replace: bool) -> ast.Node:
        name = self.parse_qualified_name()
        if self.accept_keyword("LIKE"):
            like = self.parse_qualified_name()
            return ast.CreateTable(
                name=name, like=like, replace=replace, span=self._token_span(start)
            )
        columns: list[ast.ColumnDef] = []
        constraints: list[ast.TableConstraint] = []
        periods: list[ast.DialectExpr] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")"):
                if self.at_keyword("PERIOD"):
                    self.advance()
                    period_name = self.advance().text.casefold()
                    self.expect(TokenKind.PUNCT, "(")
                    begin = self.parse_identifier()
                    self.expect(TokenKind.PUNCT, ",")
                    end = self.parse_identifier()
                    self.expect(TokenKind.PUNCT, ")")
                    periods.append(
                        ast.DialectExpr("period", {"name": period_name, "start": begin, "end": end})
                    )
                elif self._is_constraint_start():
                    constraints.append(self._parse_table_constraint())
                else:
                    columns.append(self._parse_column_db2())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        query = None
        if self.accept_keyword("AS"):
            if self.accept(TokenKind.PUNCT, "("):
                query = self.parse_query()
                self.expect(TokenKind.PUNCT, ")")
            else:
                query = self.parse_query()
        options = self._parse_table_options()
        if query is not None and any(
            key in options for key in ("refresh", "maintained_by", "query_optimization")
        ):
            options["object_type"] = "materialized_query_table"
            options["name"] = name
            options["query"] = query
            options["periods"] = periods
            return ast.DialectStatement("create_mqt", options)
        options["periods"] = periods
        return ast.CreateTable(
            name,
            columns,
            constraints,
            query,
            None,
            replace=replace,
            options=options,
            span=self._token_span(start),
        )

    def _parse_column_db2(self) -> ast.ColumnDef:
        name = self.parse_identifier()
        dtype = self.parse_data_type()
        constraints: list[ast.ColumnConstraint] = []
        default = None
        identity = None
        generated = None
        options: dict[str, Any] = {}
        while not self.at(TokenKind.PUNCT, ",") and not self.at(TokenKind.PUNCT, ")"):
            if self.accept_keyword("GENERATED"):
                self.accept_keyword("ALWAYS")
                self.accept_keyword("BY")
                self.accept_keyword("DEFAULT")
                self.expect_keyword("AS")
                if self.accept_keyword("IDENTITY"):
                    identity = ast.DialectExpr("identity", self._parse_identity_options())
                else:
                    kind = self.advance().text.casefold()
                    generated = ast.DialectExpr(f"row_{kind}", {})
                    if kind == "row" and self.at_keyword("BEGIN", "END"):
                        generated = ast.DialectExpr(f"row_{self.advance().text.casefold()}", {})
                    elif kind == "transaction":
                        self.accept_keyword("START")
                        self.accept_keyword("ID")
            elif self.accept_keyword("IDENTITY"):
                identity = ast.DialectExpr("identity", self._parse_identity_options())
            elif self.accept_keyword("WITH"):
                self.expect_keyword("DEFAULT")
                if (
                    not self.at_keyword("NOT", "NULL", "PRIMARY", "UNIQUE")
                    and not self.at(TokenKind.PUNCT, ",")
                    and not self.at(TokenKind.PUNCT, ")")
                ):
                    default = self.parse_expr(30)
            elif self.accept_keyword("DEFAULT"):
                default = self.parse_expr(30)
            elif self.accept_keyword("NOT"):
                self.expect_keyword("NULL")
                constraints.append(ast.ColumnConstraint("not_null"))
            elif self.accept_keyword("NULL"):
                constraints.append(ast.ColumnConstraint("null"))
            elif self.accept_keyword("FOR"):
                self.expect_keyword("BIT")
                self.expect_keyword("DATA")
                options["for_bit_data"] = True
            elif self.accept_keyword("IMPLICITLY"):
                self.expect_keyword("HIDDEN")
                options["implicitly_hidden"] = True
            elif self.accept_keyword("REFERENCES"):
                table = self.parse_qualified_name()
                cols = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
                constraints.append(
                    ast.ColumnConstraint("foreign_key", ref_table=table, ref_columns=cols)
                )
            else:
                break
        return ast.ColumnDef(
            name,
            dtype,
            constraints,
            default,
            identity=identity,
            generated=generated,
            options=options,
        )

    def _parse_identity_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if not self.accept(TokenKind.PUNCT, "("):
            return options
        while not self.at(TokenKind.PUNCT, ")"):
            key = self.advance().text.casefold()
            if key in {"start", "increment"}:
                self.accept_keyword("WITH") or self.accept_keyword("BY")
                options[f"{key}_with" if key == "start" else f"{key}_by"] = self.parse_expr(80)
            elif key == "no":
                options[f"no_{self.advance().text.casefold()}"] = True
            elif key in {"cycle", "order"}:
                options[key] = True
            else:
                options[key] = (
                    self.parse_expr(80) if self._is_name() or self.at(TokenKind.NUMBER) else True
                )
            if not self.accept(TokenKind.PUNCT, ",") and not self.at_keyword(
                "START", "INCREMENT", "NO", "CYCLE", "ORDER"
            ):
                break
        self.expect(TokenKind.PUNCT, ")")
        return options

    def _parse_table_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR):
            if self.accept_keyword("DISTRIBUTE"):
                self.expect_keyword("BY")
                if self.accept_keyword("RANDOM"):
                    options["distribution"] = ast.DialectExpr("distribute_by", {"random": True})
                else:
                    self.expect_keyword("HASH")
                    options["distribution"] = ast.DialectExpr(
                        "distribute_by", {"columns": self._parse_identifier_list_in_parens()}
                    )
            elif self.accept_keyword("ORGANIZE"):
                self.expect_keyword("BY")
                kind = self.advance().text.casefold()
                options["organize_by"] = ast.DialectExpr("organize_by", {"kind": kind})
            elif self.accept_keyword("IN"):
                options["tablespace"] = self.parse_qualified_name()
            elif self.accept_keyword("INDEX"):
                self.expect_keyword("IN")
                options["index_tablespace"] = self.parse_qualified_name()
            elif self.accept_keyword("LONG"):
                self.expect_keyword("IN")
                options["long_tablespace"] = self.parse_qualified_name()
            elif self.accept_keyword("COMPRESS"):
                self.accept_keyword("YES")
                options["compress"] = (
                    self.advance().text.casefold()
                    if self.at_keyword("ADAPTIVE", "STATIC")
                    else True
                )
            elif self.accept_keyword("PARTITION"):
                self.expect_keyword("BY")
                self.expect_keyword("RANGE")
                self.expect(TokenKind.PUNCT, "(")
                column = self.parse_identifier()
                self.expect(TokenKind.PUNCT, ")")
                partition: dict[str, ast.Node] = {"column": column}
                if self.accept(TokenKind.PUNCT, "("):
                    self.expect_keyword("STARTING")
                    partition["starting"] = self.parse_expr(31)
                    self.expect_keyword("ENDING")
                    partition["ending"] = self.parse_expr(31)
                    self.expect_keyword("EVERY")
                    partition["every"] = self.parse_expr(31)
                    self.expect(TokenKind.PUNCT, ")")
                options["partition_by"] = ast.DialectExpr("partition_by_range", partition)
            elif self.accept_keyword("DATA"):
                self.expect_keyword("INITIALLY")
                options["refresh"] = self.advance().text.casefold()
                if self.accept_keyword("REFRESH"):
                    options["refresh"] = self.advance().text.casefold()
            elif self.accept_keyword("ENABLE", "DISABLE"):
                options["query_optimization"] = self.tokens[self.index - 1].text.casefold()
                self.accept_keyword("QUERY")
                self.accept_keyword("OPTIMIZATION")
            elif self.accept_keyword("MAINTAINED"):
                self.expect_keyword("BY")
                options["maintained_by"] = self.advance().text.casefold()
            else:
                break
        return options

    def _parse_routine_db2(self, start: Token, kind: str) -> ast.CreateRoutine:
        name = self.parse_qualified_name()
        params: list[ast.RoutineParam] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")"):
                mode = (
                    self.advance().text.casefold()
                    if self.at_keyword("IN", "OUT", "INOUT")
                    else None
                )
                pname = self.parse_identifier()
                dtype = self.parse_data_type()
                default = self.parse_expr() if self.accept_keyword("DEFAULT") else None
                params.append(ast.RoutineParam(pname, mode, dtype, default))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        options: dict[str, Any] = {}
        returns = None
        body: ast.Node | str | None = None
        while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR):
            if self.accept_keyword("RETURNS"):
                if self.accept_keyword("TABLE"):
                    self.expect(TokenKind.PUNCT, "(")
                    children = []
                    while not self.at(TokenKind.PUNCT, ")"):
                        child_name = self.parse_identifier()
                        children.append(
                            ast.StructField(name=child_name, type=self.parse_data_type())
                        )
                        if not self.accept(TokenKind.PUNCT, ","):
                            break
                    self.expect(TokenKind.PUNCT, ")")
                    returns = ast.DataType(
                        ast.QualifiedName(parts=[ast.Identifier(value="TABLE")]), children=children
                    )
                else:
                    returns = self.parse_data_type()
            elif self.accept_keyword("LANGUAGE"):
                options["language"] = self.advance().text
            elif self.accept_keyword("SPECIFIC"):
                options["specific"] = self.parse_qualified_name()
            elif self.accept_keyword("DYNAMIC"):
                self.expect_keyword("RESULT")
                self.expect_keyword("SETS")
                options["dynamic_result_sets"] = self.parse_expr(80)
            elif self.accept_keyword("MODIFIES", "READS", "CONTAINS"):
                access = self.tokens[self.index - 1].text.casefold()
                self.accept_keyword("SQL")
                self.accept_keyword("DATA")
                options["sql_data_access"] = access
            elif self.accept_keyword("DETERMINISTIC"):
                options["deterministic"] = True
            elif self.accept_keyword("NOT"):
                if self.accept_keyword("DETERMINISTIC"):
                    options["deterministic"] = False
                elif self.accept_keyword("EXTERNAL"):
                    self.expect_keyword("ACTION")
                    options["external_action"] = False
                else:
                    break
            elif self.accept_keyword("CALLED", "RETURNS"):
                self.accept_keyword("ON")
                self.accept_keyword("NULL")
                self.accept_keyword("INPUT")
                options["null_input"] = "called"
            elif self.accept_keyword("NO"):
                self.expect_keyword("EXTERNAL")
                self.expect_keyword("ACTION")
                options["external_action"] = False
            elif self.accept_keyword("COMMIT"):
                self.expect_keyword("ON")
                self.expect_keyword("RETURN")
                options["commit_on_return"] = self.advance().text.casefold()
            elif self.accept_keyword("INHERIT"):
                self.expect_keyword("SPECIAL")
                self.expect_keyword("REGISTERS")
                options["inherit_special_registers"] = True
            elif self.accept_keyword("EXTERNAL"):
                self.expect_keyword("NAME")
                options["external_name"] = self.advance().value
            elif self.accept_keyword("PARAMETER"):
                self.expect_keyword("STYLE")
                options["parameter_style"] = self.advance().text
            elif self.accept_keyword("AS"):
                body = self._parse_routine_body()
                break
            elif self.at_keyword("BEGIN"):
                body = self._parse_routine_body()
                break
            elif self.at_keyword("RETURN"):
                body = self._parse_routine_body()
                break
            else:
                self.advance()
        return ast.CreateRoutine(
            kind,
            name,
            params,
            returns,
            options.get("language"),
            body,
            options,
            span=self._token_span(start),
        )

    def _parse_routine_body(self) -> ast.Node | str | None:
        if self.at(TokenKind.STRING) or self.at(TokenKind.DOLLAR_BODY):
            return self.advance().value
        if self.at_keyword("BEGIN"):
            return self.parse_block()
        if self.at_keyword("RETURN"):
            self.advance()
            if self.at_keyword("SELECT", "WITH"):
                return ast.Return(self.parse_query())
            return ast.Return(self.parse_expr())
        return None

    def _parse_rcac(self, start: Token, kind: str) -> ast.DialectStatement:
        name = self.parse_qualified_name()
        self.expect_keyword("ON")
        table = self.parse_qualified_name()
        correlation = self.parse_identifier().value if self.accept_keyword("AS") else None
        args: dict[str, Any] = {
            "object_type": "row_permission" if kind == "permission" else "column_mask",
            "name": name,
            "table": table,
            "correlation": correlation,
        }
        if kind == "permission":
            self.expect_keyword("FOR")
            self.expect_keyword("ROWS")
            self.expect_keyword("WHERE")
            args["where"] = self.parse_expr()
            self.accept_keyword("ENFORCED")
            if self.accept_keyword("FOR"):
                self.accept_keyword("ALL")
                self.expect_keyword("ACCESS")
        else:
            self.expect_keyword("FOR")
            self.expect_keyword("COLUMN")
            args["column"] = self.parse_identifier()
            self.expect_keyword("RETURN")
            args["return"] = self.parse_expr()
        enabled = (
            self.advance().text.casefold() if self.at_keyword("ENABLE", "DISABLE") else "enable"
        )
        args["enabled"] = enabled == "enable"
        return ast.DialectStatement(
            "create_permission" if kind == "permission" else "create_mask",
            args,
            [ast.RefHint(table, "grant_on")],
            span=self._token_span(start),
        )

    def _parse_alias(self, start: Token, kind: str) -> ast.Node:
        public = bool(self.accept_keyword("PUBLIC"))
        name = self.parse_qualified_name()
        self.expect_keyword("FOR")
        target_kind = (
            self.advance().text.casefold()
            if self.at_keyword("TABLE", "SEQUENCE", "MODULE")
            else None
        )
        target = self.parse_qualified_name()
        if kind == "synonym":
            return ast.CreateSynonym(name, target, public, span=self._token_span(start))
        return ast.DialectStatement(
            "create_alias",
            {
                "object_type": "alias",
                "name": name,
                "target": target,
                "public": public,
                "target_kind": target_kind,
            },
            [ast.RefHint(target, "synonym_for")],
            span=self._token_span(start),
        )

    def _parse_sequence(self, start: Token) -> ast.CreateSequence:
        name = self.parse_qualified_name()
        options: dict[str, Any] = {}
        if self.accept_keyword("AS"):
            options["type"] = self.parse_data_type()
        while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR):
            key = self.advance().text.casefold()
            if key in {"start", "increment"}:
                self.accept_keyword("WITH") or self.accept_keyword("BY")
                options[f"{key}_with" if key == "start" else f"{key}_by"] = self.parse_expr(80)
            elif key == "no":
                options[f"no_{self.advance().text.casefold()}"] = True
            elif key in {"cycle", "order"}:
                options[key] = True
            elif key == "cache":
                options["cache"] = self.parse_expr(80)
            else:
                self.index -= 1
                break
        return ast.CreateSequence(name, options, span=self._token_span(start))

    def _parse_index_db2(self, start: Token) -> ast.CreateIndex:
        unique = bool(self.accept_keyword("UNIQUE"))
        name = self.parse_qualified_name()
        self.expect_keyword("ON")
        table = self.parse_qualified_name()
        self.expect(TokenKind.PUNCT, "(")
        columns = []
        while not self.at(TokenKind.PUNCT, ")"):
            column = self.parse_identifier()
            direction = self.advance().text.casefold() if self.at_keyword("ASC", "DESC") else None
            columns.append(ast.DialectExpr("index_column", {"name": column, "order": direction}))
            if not self.accept(TokenKind.PUNCT, ","):
                break
        self.expect(TokenKind.PUNCT, ")")
        options: dict[str, Any] = {"columns": columns}
        if self.accept_keyword("INCLUDE"):
            options["include"] = self._parse_identifier_list_in_parens()
        options["cluster"] = bool(self.accept_keyword("CLUSTER"))
        if self.accept_keyword("ALLOW"):
            self.expect_keyword("REVERSE")
            self.expect_keyword("SCANS")
            options["allow_reverse_scans"] = True
        return ast.CreateIndex(
            name,
            table,
            [item.args["name"] for item in columns],
            unique,
            options,
            span=self._token_span(start),
        )

    def _parse_user(self, start: Token) -> ast.CreatePrincipal:
        name = self.parse_qualified_name()
        options: dict[str, Any] = {}
        if self.accept_keyword("FROM"):
            options["from"] = self.parse_qualified_name()
        while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR):
            key = self.advance().text.casefold()
            if key == "perm":
                self.accept(TokenKind.OPERATOR, "=")
                options["perm"] = self.parse_expr(80)
            elif key == "password":
                self.accept(TokenKind.OPERATOR, "=")
                self.advance()
                options["password"] = "<redacted>"
            elif key == "spool":
                self.accept(TokenKind.OPERATOR, "=")
                options["spool"] = self.parse_expr(80)
            else:
                options[key] = True
        return ast.CreatePrincipal("user", name, options, span=self._token_span(start))

    def _parse_grant_db2(self) -> ast.Grant:
        self.expect_keyword("GRANT")
        privileges: list[str] = []
        while not self.at_keyword("ON", "TO") and not self.at(TokenKind.EOF):
            first = self.advance().text
            if first.casefold() == "all":
                self.accept_keyword("PRIVILEGES")
                privileges.append("ALL")
            elif (
                first.casefold() in {"execute", "create", "alter", "drop"}
                and self._is_name()
                and not self.at_keyword("ON", "TO")
            ):
                privileges.append(f"{first} {self.advance().text}".upper())
            else:
                privileges.append(first)
            if not self.accept(TokenKind.PUNCT, ",") and self.at_keyword("ON", "TO"):
                break
        object_kind = "object"
        object_name = ast.QualifiedName()
        if self.accept_keyword("ON"):
            if self.at_keyword("table", "view", "sequence", "procedure", "function", "schema"):
                object_kind = self.advance().text.casefold()
                object_name = self.parse_qualified_name()
            else:
                object_name = self.parse_qualified_name()
        self.expect_keyword("TO")
        grantees = []
        while (
            not self.at(TokenKind.EOF)
            and not self.at(TokenKind.TERMINATOR)
            and not self.at_keyword("WITH")
        ):
            self.accept_keyword("USER", "GROUP", "ROLE")
            grantees.append(self.parse_qualified_name())
            if not self.accept(TokenKind.PUNCT, ","):
                break
        grant_option = False
        if self.accept_keyword("WITH"):
            self.expect_keyword("GRANT")
            self.expect_keyword("OPTION")
            grant_option = True
        return ast.Grant(privileges, object_kind, object_name, grantees, grant_option)

    def _parse_revoke_db2(self) -> ast.Revoke:
        self.expect_keyword("REVOKE")
        grant_option = False
        if self.accept_keyword("GRANT"):
            self.expect_keyword("OPTION")
            self.expect_keyword("FOR")
            grant_option = True
        privileges = self.parse_comma_list(lambda: self.advance().text)
        self.expect_keyword("ON")
        object_kind = self.advance().text.casefold()
        object_name = self.parse_qualified_name()
        self.expect_keyword("FROM")
        grantees = self.parse_comma_list(self.parse_qualified_name)
        return ast.Revoke(privileges, object_kind, object_name, grantees, grant_option)

    def _parse_alter_db2(self) -> ast.Alter:
        self.expect_keyword("ALTER")
        self.expect_keyword("TABLE")
        name = self.parse_qualified_name()
        if self.accept_keyword("ADD"):
            if self.accept_keyword("VERSIONING"):
                self.expect_keyword("USE")
                self.expect_keyword("HISTORY")
                self.expect_keyword("TABLE")
                action = ast.DialectExpr(
                    "add_versioning", {"history_table": self.parse_qualified_name()}
                )
            else:
                action = self._parse_column_db2()
        elif self.accept_keyword("ACTIVATE"):
            mode = self.advance().text.casefold()
            self.expect_keyword("ACCESS")
            self.expect_keyword("CONTROL")
            action = ast.DialectExpr("activate_access_control", {"mode": mode})
        else:
            action = ast.DialectStatement("alter_action", {"text": self._consume_to_end()})
        return ast.Alter("table", name, [action])

    def parse_primary_ext(self) -> ast.Node | None:
        if self.at_keyword("NEXT") and self.at_keyword("VALUE", 1):
            token = self.advance()
            self.advance()
            self.expect_keyword("FOR")
            return ast.DialectExpr(
                "next_value",
                {"sequence": self.parse_qualified_name()},
                span=self._token_span(token),
            )
        if self.at_keyword("PREVIOUS") and self.at_keyword("VALUE", 1):
            token = self.advance()
            self.advance()
            self.expect_keyword("FOR")
            return ast.DialectExpr(
                "previous_value",
                {"sequence": self.parse_qualified_name()},
                span=self._token_span(token),
            )
        if self.at_keyword("NEXTVAL", "PREVVAL") and self.at_keyword("FOR", 1):
            token = self.advance()
            self.advance()
            return ast.DialectExpr(
                "next_value" if token.text.casefold() == "nextval" else "previous_value",
                {"sequence": self.parse_qualified_name()},
                span=self._token_span(token),
            )
        return super().parse_primary_ext()

    def parse_query(self) -> ast.Query:
        self._query_depth += 1
        try:
            query = super().parse_query()
        finally:
            self._query_depth -= 1
        if self._query_depth == 0:
            self._parse_query_hints(query)
        return query

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
            count = None if self.at_keyword("ROW", "ROWS") else self.parse_expr(80)
            percent = bool(self.accept_keyword("PERCENT"))
            self.accept_keyword("ROW") or self.accept_keyword("ROWS")
            self.expect_keyword("ONLY")
            if self.at_keyword("WITH") and self.at_keyword("TIES", 1):
                self.advance()
                self.advance()
                ties = True
        return ast.Limit(count, offset, fetch, ties, percent)

    def _parse_query_hints(self, query: ast.Query) -> None:
        select = self._last_select(query.body)
        if select is None:
            return
        while True:
            if self.accept_keyword("WITH") and self.at_keyword("UR", "CS", "RS", "RR"):
                select.hints.append(f"with {self.advance().text.casefold()}")
            elif self.accept_keyword("FOR"):
                mode = self.advance().text.casefold()
                if mode == "read":
                    self.expect_keyword("ONLY")
                    select.hints.append("for read only")
                elif mode == "update":
                    text = "for update"
                    if self.accept_keyword("OF"):
                        cols = self.parse_comma_list(self.parse_identifier)
                        text += " of " + ", ".join(col.value for col in cols)
                    select.hints.append(text)
                else:
                    self.index -= 2
                    break
            elif self.accept_keyword("OPTIMIZE"):
                self.expect_keyword("FOR")
                count = self.parse_expr(80)
                self.expect_keyword("ROWS")
                value = count.value if isinstance(count, ast.Literal) else str(count)
                select.hints.append(f"optimize for {value} rows")
            else:
                break

    def _last_select(self, node: ast.Node) -> ast.Select | None:
        if isinstance(node, ast.Select):
            return node
        if isinstance(node, ast.SetOperation):
            return self._last_select(
                node.right.body if isinstance(node.right, ast.Query) else node.right
            )
        if isinstance(node, ast.Subquery):
            return self._last_select(node.query.body)
        return None

    def _parse_from_item(self) -> ast.Node:
        lateral = bool(self.accept_keyword("LATERAL"))
        start_token = self.peek()
        if self.accept(TokenKind.PUNCT, "("):
            if self.at_keyword("SELECT") or self.at_keyword("WITH") or self.at_keyword("VALUES"):
                query = self.parse_query()
                self.expect(TokenKind.PUNCT, ")")
                alias = self._parse_optional_alias()
                return ast.SubqueryRef(query, alias, lateral, span=self._token_span(start_token))
            nested = self._parse_join()
            self.expect(TokenKind.PUNCT, ")")
            return nested
        if self.at_keyword("VALUES"):
            values = self.parse_values()
            alias = self._parse_optional_alias()
            columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
            return ast.ValuesRef(values, alias, columns, span=self._token_span(start_token))
        name = self.parse_qualified_name()
        if self.at(TokenKind.PUNCT, "("):
            call = self._parse_postfix(ast.ColumnRef(name))
            alias = self._parse_optional_alias()
            return ast.TableFunctionRef(call, alias, lateral, span=self._token_span(start_token))
        alias = self._parse_optional_alias()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        temporal = None
        if self.at_keyword("FOR") and self.at_keyword("SYSTEM_TIME", 1):
            self.advance()
            self.advance()
            temporal_args: dict[str, ast.Node | str | None] = {"kind": "system_time"}
            if self.accept_keyword("AS"):
                self.expect_keyword("OF")
                temporal_args.update(
                    {
                        "mode": "as_of",
                        "operator": "of",
                        "value": self.parse_expr(31),
                    }
                )
            elif self.accept_keyword("BETWEEN"):
                start = self.parse_expr(31)
                self.expect_keyword("AND")
                temporal_args.update(
                    {
                        "operator": "between",
                        "value": start,
                        "end": self.parse_expr(31),
                    }
                )
            else:
                self.expect_keyword("FROM")
                start = self.parse_expr(31)
                self.expect_keyword("TO")
                temporal_args.update(
                    {
                        "operator": "from",
                        "value": start,
                        "end": self.parse_expr(31),
                    }
                )
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
            span=self._token_span(start_token),
        )


__all__ = ["Db2Lexer", "Parser"]
