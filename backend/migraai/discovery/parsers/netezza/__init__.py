from __future__ import annotations

from typing import Any

from migraai.discovery import ast
from migraai.discovery.lexer import LexerConfig, Token, TokenKind
from migraai.discovery.parser_base import ParseError, ParserBase


class Parser(ParserBase):
    case_rule = "upper"
    builtin_functions = ParserBase.ansi_builtins | frozenset(
        {
            "DATE_TRUNC",
            "DECODE",
            "ISNULL",
            "LENGTH",
            "NOW",
            "NVL",
            "TO_CHAR",
            "TO_DATE",
            "TO_NUMBER",
            "VARCHAR",
        }
    )
    lexer_config = LexerConfig(
        ident_quotes={'"': '"'},
        dot_command_prefixes=("\\",),
        param_styles=frozenset({"question", "colon", "dollar_numeric"}),
        extra_operators=("::", ".."),
    )

    def __init__(self, text: str | None = None, rel_path: str = "") -> None:
        super().__init__(text, rel_path, lexer_config=self.lexer_config)
        self.default_schema: str | None = None

    def parse(self, text: str | None = None, rel_path: str | None = None) -> ast.ParseResult:
        result = super().parse(text, rel_path)
        self.default_schema = None
        for statement in result.statements:
            if isinstance(statement, ast.Use):
                self.default_schema = ".".join(part.value for part in statement.database.parts)
        return result

    def parse_qualified_name(self) -> ast.QualifiedName:
        start = self.peek()
        parts = [self.parse_identifier()]
        while True:
            if self.accept(TokenKind.OPERATOR, ".."):
                parts.extend([ast.Identifier(value=""), self.parse_identifier()])
            elif self.accept(TokenKind.PUNCT, "."):
                if self.accept(TokenKind.OPERATOR, "*"):
                    parts.append(
                        ast.Identifier(
                            value="*", span=self._token_span(self.tokens[self.index - 1])
                        )
                    )
                    break
                parts.append(self.parse_identifier())
            else:
                break
        return ast.QualifiedName(
            parts=parts, span=self._range_span(start, self.tokens[self.index - 1])
        )

    def _can_be_alias(self) -> bool:
        return (
            not self.at_keyword("DISTRIBUTE", "ORGANIZE", "MINUS", "END_PROC")
            and super()._can_be_alias()
        )

    def parse_statement_ext(self) -> ast.Node | None:
        if self.at_keyword("SET") and self.at_keyword("SCHEMA", 1):
            return self._parse_set_schema()
        if self.at_keyword("GROOM"):
            return self._parse_groom()
        if self.at_keyword("GENERATE"):
            return self._parse_generate_statistics()
        if self.at_keyword("REFRESH") and self.at_keyword("MATERIALIZED", 1):
            start = self.advance()
            self.advance()
            self.expect_keyword("VIEW")
            view = self.parse_qualified_name()
            return ast.DialectStatement(
                "refresh_materialized_view",
                {"view": view},
                [ast.RefHint(view, "writes")],
                span=self._token_span(start),
            )
        if self.at_keyword("ALTER") and self.at_keyword("USER", "GROUP", 1):
            return self._parse_alter_principal()
        if self.at_keyword("GRANT"):
            return self._parse_grant_nz()
        if self.at_keyword("REVOKE"):
            return self._parse_revoke_nz()
        if self.at_keyword("RAISE"):
            return self._parse_raise()
        if self.at_keyword("EXIT") and self.at_keyword("WHEN", 1):
            self.advance()
            self.advance()
            condition = self.parse_expr()
            return ast.If(branches=[(condition, [ast.Exit()])])
        if self.at_keyword("EXECUTE") and self.at_keyword("IMMEDIATE", 1):
            self.advance()
            self.advance()
            return ast.DynamicSql(sql_expr=self.parse_expr())
        if (
            self._is_name()
            and self.index + 1 < len(self.tokens)
            and self.tokens[self.index + 1].kind is TokenKind.OPERATOR
            and self.tokens[self.index + 1].text == ":="
        ):
            target = self.parse_expr(80)
            self.expect(TokenKind.OPERATOR, ":=")
            return ast.Assign(target, self.parse_expr())
        if self.at_keyword("CALL"):
            self.advance()
            name = self.parse_qualified_name()
            args = (
                self.parse_comma_list(self.parse_expr) if self.accept(TokenKind.PUNCT, "(") else []
            )
            if args or self.tokens[self.index - 1].text == "(":
                self.expect(TokenKind.PUNCT, ")")
            return ast.Call(name, args)
        return None

    def parse_create(self) -> ast.Node:
        start = self.expect_keyword("CREATE")
        replace = False
        if self.accept_keyword("OR"):
            self.expect_keyword("REPLACE")
            replace = True
        temporary = bool(self.accept_keyword("TEMP", "TEMPORARY"))
        external = bool(self.accept_keyword("EXTERNAL"))
        if self.accept_keyword("TABLE"):
            return self._parse_create_table_nz(start, temporary, external, replace)
        if self.accept_keyword("MATERIALIZED"):
            self.expect_keyword("VIEW")
            return self._parse_create_view(start, replace, materialized=True)
        if self.accept_keyword("VIEW"):
            return self._parse_create_view(start, replace)
        if self.accept_keyword("SEQUENCE"):
            return self._parse_sequence_nz(start)
        if self.accept_keyword("SYNONYM"):
            name = self.parse_qualified_name()
            self.expect_keyword("FOR")
            return ast.CreateSynonym(
                name, self.parse_qualified_name(), span=self._token_span(start)
            )
        if self.accept_keyword("USER"):
            return self._parse_create_principal(start, "user")
        if self.accept_keyword("GROUP"):
            return self._parse_create_principal(start, "group")
        if self.at_keyword("PROCEDURE", "FUNCTION"):
            kind = self.advance().text.casefold()
            return self._parse_nz_routine(start, kind)
        if self.accept_keyword("AGGREGATE"):
            name = self.parse_qualified_name()
            options: dict[str, Any] = {}
            while not self._statement_end():
                key = self.advance().text.casefold()
                options[key] = self.parse_expr() if not self._statement_end() else True
            return ast.DialectStatement(
                "create_aggregate",
                {"object_type": "aggregate", "name": name, "options": options},
                span=self._token_span(start),
            )
        if self.accept_keyword("LIBRARY"):
            name = self.parse_qualified_name()
            location = self.parse_expr()
            return ast.DialectStatement(
                "create_library",
                {"object_type": "library", "name": name, "location": location},
                span=self._token_span(start),
            )
        raise ParseError("Unsupported CREATE statement.", self.peek())

    def _parse_create_table_nz(
        self, start: Token, temporary: bool, external: bool, replace: bool
    ) -> ast.Node:
        if external and self.at(TokenKind.STRING):
            path = self.advance()
            using = self._parse_using_options()
            self.expect_keyword("AS")
            query = self.parse_query()
            return ast.DialectStatement(
                "external_unload",
                {
                    "object_type": "etl_job",
                    "name": ast.Identifier(value="external_unload"),
                    "path": ast.Literal(type="string", value=path.value),
                    "using": using,
                    "query": query,
                },
                [
                    ast.RefHint(
                        ast.QualifiedName([ast.Identifier(value="external_unload")]), "writes"
                    )
                ],
                span=self._token_span(start),
            )
        if_not_exists = False
        if self.accept_keyword("IF"):
            self.expect_keyword("NOT")
            self.expect_keyword("EXISTS")
            if_not_exists = True
        name = self.parse_qualified_name()
        columns: list[ast.ColumnDef] = []
        constraints: list[ast.TableConstraint] = []
        like = None
        if external and self.accept_keyword("SAMEAS"):
            like = self.parse_qualified_name()
        elif self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                if self._is_constraint_start():
                    constraints.append(self._parse_table_constraint())
                else:
                    columns.append(self._parse_column_def())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        as_query = self.parse_query() if self.accept_keyword("AS") else None
        options: dict[str, Any] = {}
        while True:
            if self.accept_keyword("DISTRIBUTE"):
                self.expect_keyword("ON")
                if self.accept_keyword("RANDOM"):
                    options["distribution"] = ast.DialectExpr("distribute_on", {"random": True})
                else:
                    self.expect(TokenKind.PUNCT, "(")
                    columns_for_distribution = self.parse_comma_list(self.parse_identifier)
                    self.expect(TokenKind.PUNCT, ")")
                    options["distribution"] = ast.DialectExpr(
                        "distribute_on", {"columns": columns_for_distribution}
                    )
            elif self.accept_keyword("ORGANIZE"):
                self.expect_keyword("ON")
                if self.accept_keyword("NONE"):
                    options["organize_on"] = ast.DialectExpr("organize_on", {"none": True})
                else:
                    self.expect(TokenKind.PUNCT, "(")
                    organize_columns = self.parse_comma_list(self.parse_identifier)
                    self.expect(TokenKind.PUNCT, ")")
                    options["organize_on"] = ast.DialectExpr(
                        "organize_on", {"columns": organize_columns}
                    )
            elif external and self.at_keyword("USING"):
                options["using"] = self._parse_using_options()
            else:
                break
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
            options=options,
            span=self._token_span(start),
        )

    def _parse_using_options(self) -> dict[str, ast.Node | list[ast.Node] | str | None]:
        self.expect_keyword("USING")
        self.expect(TokenKind.PUNCT, "(")
        options: dict[str, ast.Node | list[ast.Node] | str | None] = {}
        while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
            key = self.advance().text.casefold()
            if self.accept(TokenKind.PUNCT, "("):
                value: ast.Node | list[ast.Node] | str | None = (
                    self.parse_comma_list(self.parse_expr)
                    if not self.at(TokenKind.PUNCT, ")")
                    else []
                )
                self.expect(TokenKind.PUNCT, ")")
            elif (
                self.at(TokenKind.STRING)
                or self.at(TokenKind.NUMBER)
                or self.at(TokenKind.PARAM)
                or self.at_keyword("TRUE", "FALSE", "NULL")
            ):
                value = self.parse_expr(80)
            elif self.at(TokenKind.IDENT) or self.at(TokenKind.QUOTED_IDENT):
                value = self.parse_identifier()
            else:
                value = ast.Literal(type="boolean", value=True)
            options[key] = value
        self.expect(TokenKind.PUNCT, ")")
        return options

    def _parse_create_view(
        self, start: Token, replace: bool, materialized: bool = False
    ) -> ast.CreateView:
        view = self._parse_create_view_base(start, replace, materialized)
        return view

    def _parse_create_view_base(
        self, start: Token, replace: bool, materialized: bool
    ) -> ast.CreateView:
        name = self.parse_qualified_name()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        self.expect_keyword("AS")
        query = self.parse_query()
        return ast.CreateView(
            name, columns, query, materialized, replace, span=self._token_span(start)
        )

    def _parse_sequence_nz(self, start: Token) -> ast.CreateSequence:
        name = self.parse_qualified_name()
        options: dict[str, Any] = {}
        if self.accept_keyword("AS"):
            options["data_type"] = self.parse_data_type()
        while not self._statement_end():
            if self.accept_keyword("START"):
                self.accept_keyword("WITH")
                options["start_with"] = self.parse_expr(80)
            elif self.accept_keyword("INCREMENT"):
                self.accept_keyword("BY")
                options["increment_by"] = self.parse_expr(80)
            elif self.accept_keyword("NO"):
                options[f"no_{self.advance().text.casefold()}"] = True
            elif self.accept_keyword("CYCLE"):
                options["cycle"] = True
            else:
                options[self.advance().text.casefold()] = True
        return ast.CreateSequence(name, options, span=self._token_span(start))

    def _parse_nz_routine(self, start: Token, kind: str) -> ast.CreateRoutine:
        name = self.parse_qualified_name()
        params: list[ast.RoutineParam] = []
        if self.accept(TokenKind.PUNCT, "("):
            index = 1
            while not self.at(TokenKind.PUNCT, ")"):
                params.append(
                    ast.RoutineParam(
                        ast.Identifier(value=f"${index}"), None, self.parse_data_type()
                    )
                )
                index += 1
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        returns = None
        options: dict[str, Any] = {}
        language = None
        if self.accept_keyword("RETURNS"):
            if self.accept_keyword("REFTABLE"):
                self.expect(TokenKind.PUNCT, "(")
                options["returns_reftable"] = self.parse_qualified_name()
                self.expect(TokenKind.PUNCT, ")")
            else:
                returns = self.parse_data_type()
        if self.accept_keyword("EXECUTE"):
            self.expect_keyword("AS")
            options["execute_as"] = self.advance().text.casefold()
        if self.accept_keyword("LANGUAGE"):
            language = self.advance().text
        self.expect_keyword("AS")
        body: ast.Node | str | None = None
        if self.accept_keyword("BEGIN_PROC"):
            declarations: list[ast.Node] = []
            if self.accept_keyword("DECLARE"):
                while not self.at_keyword("BEGIN") and not self.at(TokenKind.EOF):
                    declarations.append(self._parse_declare_after_keyword())
                    if not self.accept(TokenKind.TERMINATOR):
                        break
            block = self.parse_block() if self.at_keyword("BEGIN") else ast.Block()
            block.declarations[:0] = declarations
            self.accept(TokenKind.TERMINATOR)
            self.expect_keyword("END_PROC")
            body = block
        elif self.at(TokenKind.STRING) or self.at(TokenKind.DOLLAR_BODY):
            body = self.advance().value
        return ast.CreateRoutine(
            kind,
            name,
            params,
            returns,
            language or ("NZPLSQL" if isinstance(body, ast.Block) else None),
            body,
            options,
            span=self._token_span(start),
        )

    def _parse_declare_after_keyword(self) -> ast.Node:
        name = self.parse_identifier()
        if self.accept_keyword("CONSTANT"):
            datatype = self.parse_data_type()
            default = self.parse_expr() if self.accept(TokenKind.OPERATOR, ":=") else None
            return ast.Declare(name, datatype, default)
        if self.accept_keyword("ALIAS"):
            self.expect_keyword("FOR")
            param = self.advance()
            return ast.Declare(name, None, ast.Param(text=param.text))
        datatype = self.parse_data_type()
        default = None
        if self.accept_keyword("DEFAULT") or self.accept(TokenKind.OPERATOR, ":="):
            default = self.parse_expr()
        return ast.Declare(name, datatype, default)

    def parse_while(self) -> ast.While:
        self.expect_keyword("WHILE")
        condition = self.parse_expr()
        if not (self.accept_keyword("DO") or self.accept_keyword("LOOP")):
            raise ParseError("Expected DO or LOOP.", self.peek())
        body = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.accept_keyword("LOOP") or self.accept_keyword("WHILE")
        return ast.While(condition, body)

    def _parse_create_principal(self, start: Token, kind: str) -> ast.CreatePrincipal:
        name = self.parse_qualified_name()
        options: dict[str, Any] = {}
        if kind == "user":
            while not self._statement_end():
                if self.accept_keyword("WITH"):
                    if self.accept_keyword("PASSWORD"):
                        self.advance()
                        options["password"] = "<redacted>"
                    elif self.accept_keyword("DEFPRIV"):
                        options["defpriv"] = self._parse_identifier_list_in_parens()
                    else:
                        break
                elif self.accept_keyword("IN"):
                    self.expect_keyword("GROUP")
                    options["in_group"] = self.parse_comma_list(self.parse_identifier)
                elif self.accept_keyword("VALID"):
                    self.expect_keyword("UNTIL")
                    options["valid_until"] = self.parse_expr()
                else:
                    break
        elif self.accept_keyword("WITH"):
            self.expect_keyword("USER")
            options["users"] = self.parse_comma_list(self.parse_identifier)
        return ast.CreatePrincipal(kind, name, options, span=self._token_span(start))

    def _parse_alter_principal(self) -> ast.Alter:
        self.expect_keyword("ALTER")
        kind = self.advance().text.casefold()
        name = self.parse_qualified_name()
        action = self.advance().text.casefold()
        member_kind = self.advance().text.casefold() if self.at_keyword("USER", "GROUP") else "user"
        member = self.parse_qualified_name()
        return ast.Alter(
            kind,
            name,
            [ast.DialectExpr(f"{action}_{member_kind}", {"member": member})],
        )

    def _parse_grant_nz(self) -> ast.Grant:
        self.expect_keyword("GRANT")
        privileges = self._parse_nz_privileges()
        object_kind = "system"
        object_name = ast.QualifiedName()
        if self.accept_keyword("ON"):
            if self.at_keyword("TABLE", "VIEW", "SEQUENCE", "DATABASE", "SCHEMA"):
                object_kind = self.advance().text.casefold()
                if not self.at_keyword("TO"):
                    object_name = self.parse_qualified_name()
            else:
                object_kind = "object"
                object_name = self.parse_qualified_name()
        self.expect_keyword("TO")
        grantees = self._parse_nz_grantees()
        grant_option = False
        if self.accept_keyword("WITH"):
            self.expect_keyword("GRANT")
            self.expect_keyword("OPTION")
            grant_option = True
        return ast.Grant(privileges, object_kind, object_name, grantees, grant_option)

    def _parse_revoke_nz(self) -> ast.Revoke:
        self.expect_keyword("REVOKE")
        grant_option = False
        if self.accept_keyword("GRANT"):
            self.expect_keyword("OPTION")
            self.expect_keyword("FOR")
            grant_option = True
        privileges = self._parse_nz_privileges()
        self.expect_keyword("ON")
        if self.at_keyword("TABLE", "VIEW", "SEQUENCE", "DATABASE", "SCHEMA"):
            object_kind = self.advance().text.casefold()
        else:
            object_kind = "object"
        object_name = ast.QualifiedName()
        if not self.at_keyword("FROM"):
            object_name = self.parse_qualified_name()
        self.expect_keyword("FROM")
        grantees = self._parse_nz_grantees()
        return ast.Revoke(privileges, object_kind, object_name, grantees, grant_option)

    def _parse_nz_privileges(self) -> list[str]:
        privileges = []
        while not self.at_keyword("ON", "TO", "FROM", "EOF") and not self.at(TokenKind.EOF):
            words = [self.advance().text]
            if words[0].casefold() == "create" and self.at_keyword("TABLE", "VIEW", "EXTERNAL"):
                words.append(self.advance().text)
                if words[-1].casefold() == "external" and self.accept_keyword("TABLE"):
                    words.append("TABLE")
            elif words[0].casefold() == "all":
                self.accept_keyword("PRIVILEGES")
            privileges.append(" ".join(words))
            if not self.accept(TokenKind.PUNCT, ","):
                break
        return privileges

    def _parse_nz_grantees(self) -> list[ast.QualifiedName]:
        grantees = []
        while True:
            self.accept_keyword("USER", "GROUP")
            grantees.append(self.parse_qualified_name())
            if not self.accept(TokenKind.PUNCT, ","):
                return grantees

    def _parse_groom(self) -> ast.DialectStatement:
        start = self.expect_keyword("GROOM")
        self.expect_keyword("TABLE")
        table = self.parse_qualified_name()
        options: dict[str, ast.Node | str | bool | None] = {"table": table}
        while not self._statement_end():
            if self.accept_keyword("RECORDS"):
                options["records"] = self.advance().text.casefold()
            elif self.accept_keyword("PAGES"):
                options["pages"] = self.advance().text.casefold()
            elif self.accept_keyword("VERSIONS"):
                options["versions"] = True
            elif self.accept_keyword("RECLAIM"):
                self.expect_keyword("BACKUPSET")
                options["reclaim_backupset"] = (
                    self.parse_expr(80)
                    if not self.at_keyword("DEFAULT", "NONE")
                    else self.advance().text.casefold()
                )
            else:
                break
        return ast.DialectStatement(
            "groom_table",
            options,
            [ast.RefHint(table, "writes")],
            span=self._token_span(start),
        )

    def _parse_generate_statistics(self) -> ast.DialectStatement:
        start = self.expect_keyword("GENERATE")
        express = bool(self.accept_keyword("EXPRESS"))
        self.expect_keyword("STATISTICS")
        table = None
        columns = []
        if self.accept_keyword("ON"):
            table = self.parse_qualified_name()
            if self.accept(TokenKind.PUNCT, "("):
                columns = self.parse_comma_list(self.parse_identifier)
                self.expect(TokenKind.PUNCT, ")")
        return ast.DialectStatement(
            "generate_statistics",
            {"express": express, "table": table, "columns": columns},
            [ast.RefHint(table, "reads")] if table is not None else [],
            span=self._token_span(start),
        )

    def _parse_raise(self) -> ast.Raise:
        self.expect_keyword("RAISE")
        level = (
            self.advance().text.casefold()
            if self.at_keyword("DEBUG", "LOG", "INFO", "NOTICE", "WARNING", "EXCEPTION")
            else "raise"
        )
        args = self.parse_comma_list(self.parse_expr) if not self._statement_end() else []
        format_string = args[0] if args else None
        return ast.Raise(
            ast.DialectExpr("raise", {"level": level, "format": format_string, "args": args[1:]})
        )

    def _parse_set_schema(self) -> ast.Use:
        self.expect_keyword("SET")
        self.expect_keyword("SCHEMA")
        self.accept(TokenKind.OPERATOR, "=")
        return ast.Use(database=self.parse_qualified_name())

    def parse_primary_ext(self) -> ast.Node | None:
        if self.at_keyword("NEXT") and self.at_keyword("VALUE", 1):
            self.advance()
            self.advance()
            self.expect_keyword("FOR")
            return ast.DialectExpr("next_value", {"sequence": self.parse_qualified_name()})
        if self.at_keyword("NEXTVAL") and self.at_keyword("FOR", 1):
            self.advance()
            self.advance()
            return ast.DialectExpr("next_value", {"sequence": self.parse_qualified_name()})
        if self.at_keyword("PREVIOUS") and self.at_keyword("VALUE", 1):
            self.advance()
            self.advance()
            self.expect_keyword("FOR")
            return ast.DialectExpr("previous_value", {"sequence": self.parse_qualified_name()})
        return None

    def _parse_from_item(self) -> ast.Node:
        if not self.at_keyword("EXTERNAL"):
            return super()._parse_from_item()
        start = self.advance()
        path = self.expect(TokenKind.STRING)
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        like = self.parse_qualified_name() if self.accept_keyword("SAMEAS") else None
        using = self._parse_using_options()
        target = ast.QualifiedName(parts=[ast.Identifier(value=path.value)])
        return ast.DialectStatement(
            "external_table_ref",
            {
                "path": ast.Literal(type="string", value=path.value),
                "columns": columns,
                "like": like,
                "using": using,
            },
            [ast.RefHint(target, "reads")],
            span=self._token_span(start),
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
        bodies: list[ast.Node] = [self._parse_query_body()]
        operators: list[tuple[str, bool]] = []
        precedence = {"union": 1, "except": 1, "minus": 1, "intersect": 2}
        while self.at_keyword("UNION", "INTERSECT", "EXCEPT", "MINUS"):
            operator_token = self.advance()
            operator = (
                "minus"
                if operator_token.text.casefold() == "minus"
                else operator_token.text.casefold()
            )
            all_rows = bool(self.accept_keyword("ALL"))
            if not all_rows:
                self.accept_keyword("DISTINCT")
            while operators and precedence[operators[-1][0]] >= precedence[operator]:
                previous, previous_all = operators.pop()
                right = bodies.pop()
                left = bodies.pop()
                bodies.append(
                    ast.SetOperation(
                        previous, previous_all, self._as_query(left), self._as_query(right)
                    )
                )
            operators.append((operator, all_rows))
            bodies.append(self._parse_query_body())
        while operators:
            operator, all_rows = operators.pop()
            right = bodies.pop()
            left = bodies.pop()
            bodies.append(
                ast.SetOperation(operator, all_rows, self._as_query(left), self._as_query(right))
            )
        body = bodies[0]
        order_by = self._parse_order_by() if self.accept_keyword("ORDER") else []
        limit = self._parse_limit() if self._is_limit_start() else None
        return ast.Query(with_clause, body, order_by, limit, span=self._token_span(start))

    def _recover(self, start: int) -> None:
        begin_proc = next(
            (
                index
                for index in range(start, min(self.index, len(self.tokens)))
                if self.tokens[index].text.casefold() == "begin_proc"
            ),
            None,
        )
        if begin_proc is not None:
            end_proc = next(
                (
                    index
                    for index in range(max(self.index, begin_proc + 1), len(self.tokens) - 1)
                    if self.tokens[index].text.casefold() == "end_proc"
                ),
                None,
            )
            if end_proc is not None:
                self.index = end_proc + 1
                self.accept(TokenKind.TERMINATOR)
                return
        return super()._recover(start)


__all__ = ["Parser"]
