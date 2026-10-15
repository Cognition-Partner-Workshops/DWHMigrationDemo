from __future__ import annotations

import re
from typing import Any

from migraai.discovery import ast
from migraai.discovery.lexer import LexerConfig, Token, TokenKind
from migraai.discovery.parser_base import ParseError, ParserBase

from .psm import PsmMixin


class Parser(PsmMixin, ParserBase):
    case_rule = "insensitive"
    builtin_functions = ParserBase.ansi_builtins | frozenset(
        {
            "ADD_MONTHS",
            "OREPLACE",
            "ZEROIFNULL",
            "NULLIFZERO",
            "TO_CHAR",
            "TO_DATE",
            "TO_TIMESTAMP",
            "HASHROW",
            "HASHBUCKET",
            "CSUM",
            "MSUM",
            "MAVG",
            "MDIFF",
            "RANK",
            "ROW_NUMBER",
            "RANDOM",
            "TD_NORMALIZE_OVERLAP",
            "VAR_SAMP",
            "VAR_POP",
            "STDDEV_SAMP",
            "STDDEV_POP",
            "CHAR2HEXINT",
            "INDEX",
            "TD_MONTH_OF_CALENDAR",
            "TD_DAY_OF_CALENDAR",
            "TD_WEEK_OF_CALENDAR",
            "TD_YEAR_OF_CALENDAR",
            "HASHAMP",
            "HASHBAKAMP",
            "NVP",
        }
    )
    binary_ops = ParserBase.binary_ops | {
        "MOD": (60, 61, "mod"),
        "**": (70, 69, "**"),
    }
    lexer_config = LexerConfig(
        ident_quotes={'"': '"'},
        dot_command_prefixes=(".",),
        extra_operators=("**",),
        param_styles=frozenset({"question", "colon", "dollar_numeric"}),
    )
    _aliases = {"SELECT": "SEL", "INSERT": "INS", "UPDATE": "UPD", "DELETE": "DEL"}

    def __init__(self, text: str | None = None, rel_path: str = "") -> None:
        super().__init__(text, rel_path, lexer_config=self.lexer_config)
        self.default_schema: str | None = None

    def parse(self, text: str | None = None, rel_path: str | None = None) -> ast.ParseResult:
        source = self.text if text is None else text
        source = re.sub(
            r"(?im)^([ \t]*\.logon[ \t]+[^,\s]+).*$",
            lambda match: match.group(1),
            source,
        )
        result = super().parse(source, rel_path)
        self.default_schema = None
        for statement in result.statements:
            if isinstance(statement, ast.Use):
                self.default_schema = ".".join(part.value for part in statement.database.parts)
        return result

    def at_keyword(self, *words: str | int) -> bool:
        if words and isinstance(words[-1], int):
            candidates = words[:-1]
            offset = words[-1]
        else:
            candidates = words
            offset = 0
        aliases = set()
        for word in candidates:
            candidate = str(word)
            aliases.add(candidate.casefold())
            if alias := self._aliases.get(candidate.upper()):
                aliases.add(alias.casefold())
        token = self.peek(int(offset))
        return token.kind is TokenKind.IDENT and token.text.casefold() in aliases

    def parse_statement_ext(self) -> ast.Node | None:
        if self.at_keyword("SELECT", "WITH"):
            self._select_into_targets = None
            query = self.parse_query()
            targets = self._select_into_targets
            if targets and (
                len(targets) > 1 or any(isinstance(target, ast.Param) for target in targets)
            ):
                return ast.DialectStatement("select_into", {"targets": targets, "query": query})
            return query
        if self.at_keyword("EXECUTE"):
            start = self.index
            self.advance()
            if self._is_name():
                name = self.parse_qualified_name()
                if self.accept(TokenKind.PUNCT, "("):
                    args = (
                        self.parse_comma_list(self.parse_expr)
                        if not self.at(TokenKind.PUNCT, ")")
                        else []
                    )
                    self.expect(TokenKind.PUNCT, ")")
                    return ast.Call(name, args)
            self.index = start
        if self.at_keyword("DATABASE"):
            self.advance()
            target = self.parse_qualified_name()
            self.default_schema = ".".join(part.value for part in target.parts)
            return ast.Use(database=target)
        if self.at_keyword("SET") and self.at_keyword("DATABASE", 1):
            self.advance()
            self.advance()
            self.accept(TokenKind.OPERATOR, "=")
            target = self.parse_qualified_name()
            self.default_schema = ".".join(part.value for part in target.parts)
            return ast.Use(database=target)
        if self.at_keyword("BT") or (
            self.at_keyword("BEGIN") and self.at_keyword("TRANSACTION", 1)
        ):
            if self.at_keyword("BEGIN"):
                self.advance()
                self.advance()
            else:
                self.advance()
            return ast.DialectStatement("begin_transaction")
        if self.at_keyword("ET") or (self.at_keyword("END") and self.at_keyword("TRANSACTION", 1)):
            self.advance()
            if self.at_keyword("TRANSACTION"):
                self.advance()
            return ast.Commit()
        if self.at_keyword("LOCKING", "LOCK"):
            return self._parse_locking()
        if self.at_keyword("COLLECT"):
            return self._parse_collect_statistics()
        return super().parse_statement_ext()

    def parse_primary_ext(self) -> ast.Node | None:
        if self.at_keyword("RANGE_N"):
            return self._parse_range_n()
        if self.at_keyword("CASE_N"):
            return self._parse_case_n()
        if self.at_keyword("CAST"):
            token = self.advance()
            self.expect(TokenKind.PUNCT, "(")
            value = self.parse_expr()
            self.expect_keyword("AS")
            datatype = self.parse_data_type()
            format_expr = None
            if self.accept_keyword("FORMAT"):
                format_expr = self._literal_or_expr()
            self.expect(TokenKind.PUNCT, ")")
            return ast.Cast(value, datatype, "cast", format_expr, span=self._token_span(token))
        return super().parse_primary_ext()

    def parse_data_type(self) -> ast.DataType:
        first_word = self.peek().text.casefold()
        interval = first_word == "interval"
        datatype = super().parse_data_type()
        if first_word in {"timestamp", "time"}:
            if self.accept_keyword("WITH"):
                datatype.name.parts.extend(
                    [ast.Identifier(value="with"), ast.Identifier(value="time")]
                )
                self.expect_keyword("TIME")
                self.expect_keyword("ZONE")
                datatype.name.parts.append(ast.Identifier(value="zone"))
            elif self.accept_keyword("WITHOUT"):
                datatype.name.parts.extend(
                    [ast.Identifier(value="without"), ast.Identifier(value="time")]
                )
                self.expect_keyword("TIME")
                if self.accept_keyword("ZONE"):
                    datatype.name.parts.append(ast.Identifier(value="zone"))
        if interval and self.at_keyword("YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND"):
            datatype.params.append(self.advance().text.casefold())
            if self.accept(TokenKind.PUNCT, "("):
                datatype.params.append(self.parse_expr(80))
                self.expect(TokenKind.PUNCT, ")")
            if self.accept_keyword("TO"):
                datatype.params.append("to")
                datatype.params.append(self.advance().text.casefold())
                if self.accept(TokenKind.PUNCT, "("):
                    datatype.params.append(self.parse_expr(80))
                    self.expect(TokenKind.PUNCT, ")")
        return datatype

    def parse_postfix_ext(self, left: ast.Node) -> ast.Node | None:
        if (
            self.index
            and self.tokens[self.index - 1].text == ")"
            and self.at_keyword("YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND")
        ):
            unit = self.advance().text.casefold()
            precision = None
            if self.accept(TokenKind.PUNCT, "("):
                precision = self.parse_expr(80)
                self.expect(TokenKind.PUNCT, ")")
            to_unit = None
            to_precision = None
            if self.accept_keyword("TO"):
                to_unit = self.advance().text.casefold()
                if self.accept(TokenKind.PUNCT, "("):
                    to_precision = self.parse_expr(80)
                    self.expect(TokenKind.PUNCT, ")")
            return ast.DialectExpr(
                "interval_qualifier",
                {
                    "expr": left,
                    "unit": unit,
                    "precision": precision,
                    "to_unit": to_unit,
                    "to_precision": to_precision,
                },
            )
        if self.at(TokenKind.PUNCT, "(") and self.index:
            previous = self.tokens[self.index - 1]
            current = self.peek()
            if current.offset > previous.offset + len(previous.text):
                saved = self.index
                self.advance()
                if self.accept_keyword("FORMAT", "TITLE", "NAMED", "CASESPECIFIC", "CS"):
                    first = self.tokens[self.index - 1].text.casefold()
                    if first in {"casespecific", "cs"}:
                        args = {"casespecific": True}
                    elif first in {"format", "title", "named"}:
                        args = {first: self._literal_or_expr()}
                    else:
                        args = {}
                    if self.accept(TokenKind.PUNCT, ","):
                        self.expect_keyword("FORMAT")
                        args["format"] = self._literal_or_expr()
                    if self.accept_keyword("NOT"):
                        self.expect_keyword("CASESPECIFIC")
                        args["casespecific"] = False
                    self.expect(TokenKind.PUNCT, ")")
                    args["expr"] = left
                    return ast.DialectExpr(
                        "td_attributes", args, span=getattr(left, "span", ast.Span())
                    )
                if self._is_name() and self.peek().text.upper() in {
                    "BYTEINT",
                    "SMALLINT",
                    "INTEGER",
                    "INT",
                    "BIGINT",
                    "DATE",
                    "TIME",
                    "TIMESTAMP",
                    "DECIMAL",
                    "NUMBER",
                    "CHAR",
                    "VARCHAR",
                }:
                    datatype = self.parse_data_type()
                    if self.accept(TokenKind.PUNCT, ","):
                        self.expect_keyword("FORMAT")
                        format_expr = self._literal_or_expr()
                    else:
                        format_expr = None
                    self.expect(TokenKind.PUNCT, ")")
                    return ast.DialectExpr(
                        "td_attributes",
                        {"expr": left, "type": datatype, "format": format_expr},
                    )
                self.index = saved
        if self.at_keyword("FORMAT", "TITLE", "NAMED", "CASESPECIFIC", "CS"):
            name = self.advance().text.casefold()
            args: dict[str, Any] = {"expr": left}
            if name in {"casespecific", "cs"}:
                args["casespecific"] = True
            else:
                args[name] = self._literal_or_expr()
            return ast.DialectExpr("td_attributes", args, span=getattr(left, "span", ast.Span()))
        if self.at_keyword("NOT") and self.at_keyword("CASESPECIFIC", 1):
            self.advance()
            self.advance()
            return ast.DialectExpr(
                "td_attributes",
                {"expr": left, "casespecific": False},
                span=getattr(left, "span", ast.Span()),
            )
        if self.at_keyword("UPPERCASE", "UC"):
            self.advance()
            return ast.DialectExpr(
                "td_attributes",
                {"expr": left, "uppercase": True},
                span=getattr(left, "span", ast.Span()),
            )
        return super().parse_postfix_ext(left)

    def parse_select(self) -> ast.Select:
        start = self.advance()
        distinct = bool(self.accept_keyword("DISTINCT"))
        distinct_on = []
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
            ties = bool(self.accept_keyword("WITH") and self.accept_keyword("TIES"))
            top = ast.Top(count, percent, ties)
        projections = self.parse_comma_list(self._parse_select_item)
        targets: list[ast.Node] = []
        into = None
        if self.accept_keyword("INTO"):
            targets = self.parse_comma_list(lambda: self.parse_expr(70))
            if (
                len(targets) == 1
                and isinstance(targets[0], ast.ColumnRef)
                and targets[0].name.parts
            ):
                into = targets[0].name
        from_items = self._parse_from_list() if self.accept_keyword("FROM") else []
        sample = None
        if self.accept_keyword("SAMPLE"):
            sample = self._parse_sample()
        where = self.parse_expr() if self.accept_keyword("WHERE") else None
        group_by = self._parse_group_by() if self.accept_keyword("GROUP") else None
        having = self.parse_expr() if self.accept_keyword("HAVING") else None
        windows = {}
        if self.accept_keyword("WINDOW"):
            while True:
                window_name = self.parse_identifier()
                self.expect_keyword("AS")
                windows[window_name.value] = self.parse_window_spec()
                if not self.accept(TokenKind.PUNCT, ","):
                    break
        qualify = self.parse_expr() if self.accept_keyword("QUALIFY") else None
        select = ast.Select(
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
            sample=sample,
            span=self._token_span(start),
        )
        self._select_into_targets = targets if targets else None
        if select.sample is None and self.accept_keyword("SAMPLE"):
            select.sample = self._parse_sample()
        return select

    def parse_query(self) -> ast.Query:
        query = super().parse_query()
        if isinstance(query.body, ast.Select) and query.body.sample is None:
            if self.accept_keyword("SAMPLE"):
                query.body.sample = self._parse_sample()
        return query

    def _parse_sample(self) -> ast.DialectExpr:
        values = [self.parse_expr(80)]
        if self.accept(TokenKind.PUNCT, ","):
            values.append(self.parse_expr(80))
        replacement = bool(self.accept_keyword("WITH") and self.accept_keyword("REPLACEMENT"))
        randomized = bool(self.accept_keyword("RANDOMIZED") and self.accept_keyword("ALLOCATION"))
        return ast.DialectExpr(
            "sample",
            {
                "values": values,
                "with_replacement": replacement,
                "randomized_allocation": randomized,
            },
        )

    def _parse_from_item(self) -> ast.Node:
        lateral = bool(self.accept_keyword("LATERAL"))
        start = self.peek()
        if self.accept(TokenKind.PUNCT, "("):
            if self.at_keyword("SELECT", "WITH", "VALUES"):
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
            call = self._parse_postfix(ast.ColumnRef(name))
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
            temporal = ast.DialectExpr("temporal", temporal_args)
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
            None,
            hints,
            span=self._token_span(start),
        )

    def parse_create(self) -> ast.Node:
        start = self.peek()
        self.expect_keyword("CREATE")
        replace = False
        if self.accept_keyword("OR"):
            self.expect_keyword("REPLACE")
            replace = True
        set_multiset = None
        if self.accept_keyword("SET", "MULTISET"):
            set_multiset = self.tokens[self.index - 1].text.casefold()
        table_kind = None
        temporary = False
        if self.accept_keyword("VOLATILE"):
            table_kind, temporary = "volatile", True
        elif self.accept_keyword("GLOBAL"):
            self.expect_keyword("TEMPORARY")
            table_kind, temporary = "global_temporary", True
        unique = bool(self.accept_keyword("UNIQUE"))
        if self.at_keyword("JOIN") and self.at_keyword("INDEX", 1):
            self.advance()
            self.advance()
            return self._parse_join_index(start, unique)
        if self.at_keyword("HASH") and self.at_keyword("INDEX", 1):
            self.advance()
            self.advance()
            return self._parse_hash_index(start)
        if self.at_keyword("TABLE"):
            self.advance()
            return self._parse_td_table(start, set_multiset, table_kind, temporary, replace)
        if self.at_keyword("MACRO", "PROCEDURE", "FUNCTION"):
            kind = self.advance().text.casefold()
            return (
                self._parse_macro(start, replace)
                if kind == "macro"
                else self._parse_teradata_routine(start, kind, replace)
            )
        if self.at_keyword("INDEX"):
            self.advance()
            return self._parse_secondary_index(start, unique)
        if self.at_keyword("VIEW"):
            self.advance()
            return self._parse_create_view(start, replace)
        if self.at_keyword("ROLE"):
            self.advance()
            name = self.parse_qualified_name()
            return ast.CreatePrincipal(
                "role", name, self._parse_options(), span=self._token_span(start)
            )
        if self.at_keyword("USER", "DATABASE"):
            kind = self.advance().text.casefold()
            name = self.parse_qualified_name()
            options = (
                self._parse_td_principal_options()
                if kind in {"user", "database"}
                else self._parse_options()
            )
            self._redact_option(options, "password")
            if kind == "database":
                return ast.CreateDatabase(name, options, span=self._token_span(start))
            return ast.CreatePrincipal("user", name, options, span=self._token_span(start))
        self.index = self._index_for_token(start)
        return super().parse_create()

    def parse_statement(self) -> ast.Node:
        if self.at_keyword("REPLACE"):
            start = self.advance()
            kind = self.advance().text.casefold()
            if kind == "view":
                node = self._parse_create_view(start, True)
            elif kind == "macro":
                node = self._parse_macro(start, True)
            elif kind in {"procedure", "function"}:
                node = self._parse_teradata_routine(start, kind, True)
            else:
                raise ParseError("Unsupported REPLACE statement.", self.peek())
            node.span = self._range_span(start, self.tokens[self.index - 1])
            return node
        return super().parse_statement()

    def _parse_teradata_routine(
        self, start: Token, kind: str, replace: bool = False
    ) -> ast.CreateRoutine:
        name = self.parse_qualified_name()
        params: list[ast.RoutineParam] = []
        options: dict[str, Any] = {"replace": replace} if replace else {}
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")"):
                mode = (
                    self.advance().text.casefold()
                    if self.at_keyword("IN", "OUT", "INOUT")
                    else None
                )
                param_name = self.parse_identifier()
                datatype = self.parse_data_type()
                default = self.parse_expr() if self.accept_keyword("DEFAULT") else None
                if self.accept_keyword("FORMAT"):
                    options.setdefault("parameter_formats", {})[param_name.value] = (
                        self._literal_or_expr()
                    )
                params.append(ast.RoutineParam(param_name, mode, datatype, default))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        returns = self.parse_data_type() if self.accept_keyword("RETURNS") else None
        language = None
        body: ast.Node | None = None
        while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR):
            if self.at_keyword("BEGIN"):
                body = self.parse_block()
                break
            if self.accept_keyword("LANGUAGE"):
                language = self.advance().text
            elif self.accept_keyword("SPECIFIC"):
                options["specific"] = self.parse_qualified_name()
            elif self.accept_keyword("DYNAMIC"):
                self.expect_keyword("RESULT")
                self.expect_keyword("SETS")
                options["dynamic_result_sets"] = self.parse_expr(80)
            elif self.accept_keyword("SQL"):
                self.expect_keyword("SECURITY")
                options["sql_security"] = self.advance().text.casefold()
            elif self.accept_keyword("DETERMINISTIC"):
                options["deterministic"] = True
            elif self.accept_keyword("CONTAINS", "MODIFIES", "READS"):
                word = self.tokens[self.index - 1].text.casefold()
                self.expect_keyword("SQL")
                if word != "contains":
                    self.expect_keyword("DATA")
                options["sql_data_access"] = word
            elif self.accept_keyword("CALLED"):
                self.expect_keyword("ON")
                self.expect_keyword("NULL")
                self.expect_keyword("INPUT")
                options["called_on_null_input"] = True
            elif self.accept_keyword("COLLATION"):
                options["collation"] = self.advance().text.casefold()
            elif self.accept_keyword("INLINE"):
                self.expect_keyword("TYPE")
                options["inline_type"] = self.parse_expr(80)
            elif self.accept_keyword("RETURN"):
                body = ast.Return(self.parse_expr())
                break
            elif self.accept_keyword("AS"):
                continue
            else:
                options.setdefault("clauses", []).append(self.advance().text.casefold())
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

    def _parse_td_principal_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        while not self._statement_end():
            if self.accept_keyword("WITH"):
                continue
            if self.accept_keyword("DEFAULT"):
                self.expect_keyword("DATABASE")
                self.accept(TokenKind.OPERATOR, "=")
                options["default_database"] = self.parse_qualified_name()
                continue
            if self.accept_keyword("FROM"):
                options["from"] = self.parse_qualified_name()
                self.accept_keyword("AS")
                continue
            if self.accept_keyword("IN"):
                self.expect_keyword("GROUP")
                options["in_group"] = [self.parse_qualified_name()]
                if self.accept(TokenKind.PUNCT, ",") and self._is_name():
                    options["in_group"].append(self.parse_qualified_name())
                continue
            key = self.advance().text.casefold()
            self.accept(TokenKind.OPERATOR, "=")
            if key == "password":
                self.advance()
                options[key] = "<redacted>"
            elif self._is_name() or self.at(TokenKind.NUMBER) or self.at(TokenKind.STRING):
                options[key] = self.parse_expr(80)
            else:
                options[key] = True
            if self.accept(TokenKind.PUNCT, ","):
                continue
            if self.at_keyword("IN", "FROM", "WITH"):
                continue
            break
        return options

    def _parse_create_view(
        self, start: Token, replace: bool, materialized: bool = False, secure: bool = False
    ) -> ast.CreateView:
        name = self.parse_qualified_name()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        self.expect_keyword("AS")
        options: dict[str, Any] = {}
        if self.at_keyword("LOCKING", "LOCK"):
            options["locking"] = self._parse_lock_specs()
        query = self.parse_query()
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

    def _parse_macro(self, start: Token, replace: bool = False) -> ast.CreateRoutine:
        name = self.parse_qualified_name()
        params: list[ast.RoutineParam] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")"):
                param_name = self.parse_identifier()
                datatype = self.parse_data_type()
                default = self.parse_expr() if self.accept_keyword("DEFAULT") else None
                if self.accept_keyword("FORMAT"):
                    self._literal_or_expr()
                params.append(ast.RoutineParam(param_name, None, datatype, default))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        self.expect_keyword("AS")
        self.expect(TokenKind.PUNCT, "(")
        statements = []
        while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
            if self.at(TokenKind.TERMINATOR):
                self.advance()
            else:
                statements.append(self.parse_statement())
                self.accept(TokenKind.TERMINATOR)
        self.expect(TokenKind.PUNCT, ")")
        return ast.CreateRoutine(
            "macro",
            name,
            params,
            body=ast.Block(statements=statements),
            options={"replace": replace},
            span=self._token_span(start),
        )

    def _parse_td_table(
        self,
        start: Token,
        set_multiset: str | None,
        table_kind: str | None,
        temporary: bool,
        replace: bool,
    ) -> ast.CreateTable:
        name = self.parse_qualified_name()
        columns: list[ast.ColumnDef] = []
        constraints: list[ast.TableConstraint] = []
        options: dict[str, Any] = {}
        if set_multiset:
            options["set_multiset"] = set_multiset
        if table_kind:
            options["table_kind"] = table_kind
        like = None
        as_query = None
        while self.accept(TokenKind.PUNCT, ","):
            if not self.at_keyword(
                "NO",
                "FALLBACK",
                "JOURNAL",
                "BEFORE",
                "AFTER",
                "CHECKSUM",
                "DEFAULT",
                "MAP",
                "FREESPACE",
                "DATABLOCKSIZE",
            ):
                self.index -= 1
                break
            options.setdefault("table_options", []).append(self._parse_table_option())
        if self.accept_keyword("AS"):
            if self.accept(TokenKind.PUNCT, "("):
                as_query = self.parse_query()
                self.expect(TokenKind.PUNCT, ")")
            else:
                like = self.parse_qualified_name()
            if self.accept_keyword("WITH"):
                with_data = not bool(self.accept_keyword("NO"))
                self.expect_keyword("DATA")
                options["with_data"] = with_data
                if self.accept_keyword("AND"):
                    options["with_statistics"] = not bool(self.accept_keyword("NO"))
                    self.expect_keyword("STATISTICS")
        elif self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                if self._is_constraint_start():
                    constraints.append(self._parse_table_constraint())
                else:
                    columns.append(self._parse_td_column())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        while not self._statement_end():
            comma = self.index
            had_comma = bool(self.accept(TokenKind.PUNCT, ","))
            if had_comma and not self.at_keyword(
                "UNIQUE",
                "PRIMARY",
                "NO",
                "INDEX",
                "PARTITION",
                "ON",
                "FALLBACK",
                "JOURNAL",
                "BEFORE",
                "AFTER",
                "CHECKSUM",
                "DEFAULT",
                "MAP",
                "FREESPACE",
                "DATABLOCKSIZE",
            ):
                self.index = comma
                break
            if self.accept_keyword("UNIQUE"):
                if self.accept_keyword("PRIMARY"):
                    self.expect_keyword("INDEX")
                    options["primary_index"] = self._parse_index_clause(unique=True)
                else:
                    self.expect_keyword("INDEX")
                    options.setdefault("secondary_indexes", []).append(
                        self._parse_index_clause(unique=True)
                    )
            elif self.accept_keyword("PRIMARY"):
                if self.accept_keyword("AMP"):
                    self.expect_keyword("INDEX")
                    options["primary_index"] = self._parse_index_clause()
                    options["primary_amp_index"] = True
                else:
                    self.expect_keyword("INDEX")
                    options["primary_index"] = self._parse_index_clause()
            elif self.at_keyword("NO") and self.at_keyword("PRIMARY", 1):
                self.advance()
                self.expect_keyword("PRIMARY")
                self.expect_keyword("INDEX")
                options["primary_index"] = "none"
            elif self.accept_keyword("INDEX"):
                options.setdefault("secondary_indexes", []).append(self._parse_index_clause())
            elif self.accept_keyword("PARTITION"):
                self.expect_keyword("BY")
                if self.accept_keyword("COLUMN"):
                    options["partitioned_by"] = [ast.DialectExpr("partition_column", {})]
                elif self.accept(TokenKind.PUNCT, "("):
                    partitions = []
                    while not self.at(TokenKind.PUNCT, ")"):
                        partitions.append(self.parse_expr())
                        if not self.accept(TokenKind.PUNCT, ","):
                            break
                    self.expect(TokenKind.PUNCT, ")")
                    options["partitioned_by"] = partitions
                else:
                    options["partitioned_by"] = [self.parse_expr()]
            elif self.accept_keyword("ON"):
                self.expect_keyword("COMMIT")
                options["on_commit"] = self.advance().text.casefold()
                self.accept_keyword("ROWS")
            elif self.at_keyword(
                "NO",
                "FALLBACK",
                "JOURNAL",
                "BEFORE",
                "AFTER",
                "CHECKSUM",
                "DEFAULT",
                "MAP",
                "FREESPACE",
                "DATABLOCKSIZE",
            ):
                options.setdefault("table_options", []).append(self._parse_table_option())
            else:
                break
        return ast.CreateTable(
            name,
            columns,
            constraints,
            as_query,
            like,
            temporary=temporary,
            replace=replace,
            options=options,
            span=self._token_span(start),
        )

    def _parse_td_column(self) -> ast.ColumnDef:
        name = self.parse_identifier()
        datatype = self.parse_data_type()
        constraints: list[ast.ColumnConstraint] = []
        options: dict[str, Any] = {}
        default = None
        identity = None
        while not self.at(TokenKind.PUNCT, ",") and not self.at(TokenKind.PUNCT, ")"):
            if self.accept_keyword("NOT"):
                if self.accept_keyword("NULL"):
                    constraints.append(ast.ColumnConstraint("not_null"))
                elif self.accept_keyword("CASESPECIFIC", "CS"):
                    options["casespecific"] = False
                else:
                    break
            elif self.accept_keyword("NULL"):
                constraints.append(ast.ColumnConstraint("null"))
            elif self.accept_keyword("DEFAULT", "VALUE"):
                default = self.parse_expr(30)
            elif self.accept_keyword("WITH"):
                self.expect_keyword("DEFAULT")
                default = self.parse_expr(30) if not self.at_keyword("NOT", "NULL") else None
            elif self.accept_keyword("PRIMARY"):
                self.expect_keyword("KEY")
                constraints.append(ast.ColumnConstraint("primary_key"))
            elif self.accept_keyword("UNIQUE"):
                constraints.append(ast.ColumnConstraint("unique"))
            elif self.accept_keyword("REFERENCES"):
                target = self.parse_qualified_name()
                cols = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
                constraints.append(
                    ast.ColumnConstraint("foreign_key", ref_table=target, ref_columns=cols)
                )
            elif self.accept_keyword("CHECK"):
                self.expect(TokenKind.PUNCT, "(")
                expr = self.parse_expr()
                self.expect(TokenKind.PUNCT, ")")
                constraints.append(ast.ColumnConstraint("check", expr=expr))
            elif self.accept_keyword("CONSTRAINT"):
                constraints.append(ast.ColumnConstraint("named", name=self.parse_identifier()))
            elif self.accept_keyword("GENERATED"):
                self.accept_keyword("ALWAYS")
                if self.accept_keyword("BY"):
                    self.expect_keyword("DEFAULT")
                self.accept_keyword("AS")
                self.expect_keyword("IDENTITY")
                identity = ast.DialectExpr("identity", self._parse_identity_options())
            elif self.accept_keyword("FORMAT", "TITLE"):
                option = self.tokens[self.index - 1].text.casefold()
                options[option] = self._literal_or_expr()
            elif self.accept_keyword("COMPRESS"):
                if self.accept(TokenKind.PUNCT, "("):
                    values = self.parse_comma_list(self._literal_or_expr)
                    self.expect(TokenKind.PUNCT, ")")
                    options["compress"] = values
                elif self._is_name() or self.at(TokenKind.STRING) or self.at(TokenKind.NUMBER):
                    options["compress"] = self._literal_or_expr()
                else:
                    options["compress"] = True
            elif self.accept_keyword("CHARACTER"):
                self.expect_keyword("SET")
                options["character_set"] = self.advance().text.casefold()
            elif self.accept_keyword("CASESPECIFIC", "CS"):
                options["casespecific"] = True
            elif self.accept_keyword("UPPERCASE", "UC"):
                options["uppercase"] = True
            else:
                break
        return ast.ColumnDef(
            name, datatype, constraints, default, identity=identity, options=options
        )

    def _parse_identity_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if not self.accept(TokenKind.PUNCT, "("):
            return options
        while not self.at(TokenKind.PUNCT, ")"):
            key = self.advance().text.casefold()
            if key == "start":
                self.accept_keyword("WITH")
                options["start_with"] = self.parse_expr(80)
            elif key == "increment":
                self.accept_keyword("BY")
                options["increment_by"] = self.parse_expr(80)
            elif key == "no" and self._is_name():
                options[f"no_{self.advance().text.casefold()}"] = True
            elif key in {"cycle", "order"}:
                options[key] = True
            else:
                options[key] = (
                    self.parse_expr(80) if self._is_name() or self.at(TokenKind.NUMBER) else True
                )
            if not self.accept(TokenKind.PUNCT, ","):
                if self.at_keyword("START", "INCREMENT", "NO", "CYCLE", "ORDER", "CACHE"):
                    continue
                break
        self.expect(TokenKind.PUNCT, ")")
        return options

    def _parse_index_clause(self, unique: bool = False) -> ast.DialectExpr:
        name = self.parse_identifier() if self._is_name() and self.peek(1).text == "(" else None
        self.expect(TokenKind.PUNCT, "(")
        columns = self._parse_identifier_list()
        return ast.DialectExpr(
            "primary_index",
            {"unique": unique, "name": name, "columns": columns},
        )

    def _parse_table_option(self) -> ast.DialectExpr:
        name = self.advance().text.casefold()
        if name == "default":
            value = self.advance().text.casefold()
            option_name = f"default_{value}"
        elif name == "no":
            following = self.advance().text.casefold()
            option_name = f"no_{following}"
            if following in {"before", "after"} and self.accept_keyword("JOURNAL"):
                option_name += "_journal"
            value = True
        elif name in {"before", "after"} and self.accept_keyword("JOURNAL"):
            option_name = f"{name}_journal"
            value = True
        elif name == "map":
            self.accept(TokenKind.OPERATOR, "=")
            value = self.parse_identifier()
            option_name = name
        elif name in {"freespace", "datablocksize", "checksum"}:
            self.accept(TokenKind.OPERATOR, "=")
            value = self.parse_expr(80)
            if self.accept_keyword("PERCENT", "BYTES"):
                value = ast.DialectExpr(
                    "value_with_unit",
                    {"value": value, "unit": self.tokens[self.index - 1].text.casefold()},
                )
            option_name = name
        else:
            option_name = name
            value = self.parse_expr(80) if self._is_name() or self.at(TokenKind.NUMBER) else True
        negated = name == "no"
        return ast.DialectExpr(
            "table_option",
            {
                "name": option_name,
                "value": value,
                "negated": negated,
                "prefix": "NO" if negated else None,
            },
        )

    def _parse_secondary_index(self, start: Token, unique: bool) -> ast.CreateIndex:
        name = (
            self.parse_qualified_name()
            if self._is_name() and not self.at_keyword("ON")
            else ast.QualifiedName()
        )
        self.expect(TokenKind.PUNCT, "(")
        columns = self.parse_comma_list(self.parse_expr)
        self.expect(TokenKind.PUNCT, ")")
        table = self.parse_qualified_name() if self.accept_keyword("ON") else ast.QualifiedName()
        return ast.CreateIndex(name, table, columns, unique, span=self._token_span(start))

    def _parse_join_index(self, start: Token, unique: bool) -> ast.DialectStatement:
        name = self.parse_qualified_name()
        table_options = []
        while self.accept(TokenKind.PUNCT, ","):
            table_options.append(self._parse_table_option())
        self.expect_keyword("AS")
        query = self.parse_query()
        options: dict[str, Any] = {
            "object_type": "join_index",
            "name": name,
            "query": query,
            "table_options": table_options,
            "unique": unique,
        }
        if self.accept_keyword("PRIMARY"):
            self.expect_keyword("INDEX")
            options["primary_index"] = self._parse_index_clause()
        if self.accept_keyword("PARTITION"):
            self.expect_keyword("BY")
            if self.accept(TokenKind.PUNCT, "("):
                options["partitioned_by"] = self.parse_comma_list(self.parse_expr)
                self.expect(TokenKind.PUNCT, ")")
            else:
                options["partitioned_by"] = [self.parse_expr(31)]
        return ast.DialectStatement("create_join_index", options, span=self._token_span(start))

    def _parse_hash_index(self, start: Token) -> ast.DialectStatement:
        name = self.parse_qualified_name()
        table_options = []
        while self.accept(TokenKind.PUNCT, ","):
            if not self.at_keyword("NO", "FALLBACK", "JOURNAL", "DEFAULT", "MAP"):
                self.index -= 1
                break
            table_options.append(self._parse_table_option())
        columns: list[ast.Identifier] = []
        if self.accept(TokenKind.PUNCT, "("):
            columns = self._parse_identifier_list()
        self.expect_keyword("ON")
        table = self.parse_qualified_name()
        by = self._parse_identifier_list_in_parens() if self.accept_keyword("BY") else []
        order_by: list[ast.Identifier] = []
        if self.accept_keyword("ORDER"):
            self.expect_keyword("BY")
            self.accept_keyword("VALUES", "HASH")
            order_by = self._parse_identifier_list_in_parens()
        return ast.DialectStatement(
            "create_hash_index",
            {
                "object_type": "hash_index",
                "name": name,
                "table": table,
                "columns": columns,
                "by": by,
                "order_by": order_by,
                "table_options": table_options,
            },
            [ast.RefHint(table, "index_on")],
            span=self._token_span(start),
        )

    def _parse_range_n(self) -> ast.DialectExpr:
        self.advance()
        self.expect(TokenKind.PUNCT, "(")
        expression = self.parse_expr(31)

        def parse_range() -> ast.DialectExpr:
            self.expect_keyword("BETWEEN")
            lower = ast.Star() if self.accept(TokenKind.OPERATOR, "*") else self.parse_expr(31)
            self.expect_keyword("AND")
            upper = ast.Star() if self.accept(TokenKind.OPERATOR, "*") else self.parse_expr(31)
            step = self.parse_expr(31) if self.accept_keyword("EACH") else None
            return ast.DialectExpr("range", {"start": lower, "end": upper, "each": step})

        ranges = [parse_range()]
        no_range = False
        or_unknown = False
        unknown = False
        while True:
            if self.accept(TokenKind.PUNCT, ","):
                if self.at_keyword("BETWEEN"):
                    ranges.append(parse_range())
                    continue
            if self.accept_keyword("NO"):
                self.expect_keyword("RANGE")
                no_range = True
                if self.accept_keyword("OR"):
                    self.expect_keyword("UNKNOWN")
                    or_unknown = True
                continue
            if self.accept_keyword("UNKNOWN"):
                unknown = True
                continue
            break
        self.expect(TokenKind.PUNCT, ")")
        return ast.DialectExpr(
            "range_n",
            {
                "expr": expression,
                "ranges": ranges,
                "no_range": no_range,
                "or_unknown": or_unknown,
                "unknown": unknown,
            },
        )

    def _parse_case_n(self) -> ast.DialectExpr:
        self.advance()
        self.expect(TokenKind.PUNCT, "(")
        expression = self.parse_expr(31)

        def parse_condition() -> ast.DialectExpr:
            self.expect_keyword("WHEN")
            condition = self.parse_expr()
            self.expect_keyword("THEN")
            return ast.DialectExpr(
                "case_when", {"condition": condition, "result": self.parse_expr()}
            )

        conditions = [parse_condition()]
        no_case = False
        or_unknown = False
        unknown = False
        while True:
            self.accept(TokenKind.PUNCT, ",")
            if self.at_keyword("WHEN"):
                conditions.append(parse_condition())
                continue
            if self.accept_keyword("NO"):
                self.expect_keyword("CASE")
                no_case = True
                if self.accept_keyword("OR"):
                    self.expect_keyword("UNKNOWN")
                    or_unknown = True
                continue
            if self.accept_keyword("UNKNOWN"):
                unknown = True
                continue
            break
        self.expect(TokenKind.PUNCT, ")")
        return ast.DialectExpr(
            "case_n",
            {
                "expr": expression,
                "conditions": conditions,
                "no_case": no_case,
                "or_unknown": or_unknown,
                "unknown": unknown,
            },
        )

    def _parse_locking(self) -> ast.DialectStatement:
        locks = self._parse_lock_specs()
        statement = self.parse_statement()
        return ast.DialectStatement("locking", {"locks": locks, "statement": statement})

    def _parse_lock_specs(self) -> list[ast.DialectExpr]:
        locks = []
        while self.accept_keyword("LOCKING", "LOCK"):
            kind = self.advance().text.casefold()
            name = None if self.at_keyword("FOR", "IN") else self.parse_qualified_name()
            if not (self.accept_keyword("FOR") or self.accept_keyword("IN")):
                raise ParseError("Expected FOR or IN in LOCKING clause.", self.peek())
            mode = self.advance().text.casefold()
            nowait = bool(self.accept_keyword("NOWAIT"))
            locks.append(
                ast.DialectExpr(
                    "lock", {"kind": kind, "name": name, "mode": mode, "nowait": nowait}
                )
            )
        return locks

    def _parse_collect_statistics(self) -> ast.DialectStatement:
        start = self.expect_keyword("COLLECT")
        self.expect_keyword("STATISTICS", "STATS", "STAT")
        using: dict[str, Any] = {}
        if self.accept_keyword("USING"):
            while not self.at_keyword("COLUMN", "INDEX", "ON") and not self._statement_end():
                key = self.advance().text.casefold()
                using[key] = (
                    self.parse_expr(80) if self._is_name() or self.at(TokenKind.NUMBER) else True
                )
                if key == "sample" and self.accept_keyword("PERCENT"):
                    using["percent"] = True
        columns: list[list[ast.Identifier]] = []
        indexes: list[ast.Node] = []
        while not self.at_keyword("ON") and not self._statement_end():
            if self.accept_keyword("COLUMN"):
                if self.accept(TokenKind.PUNCT, "("):
                    columns.append(self._parse_identifier_list())
                else:
                    columns.append([self.parse_identifier()])
            elif self.accept_keyword("INDEX"):
                indexes.append(
                    self._parse_identifier_list_in_parens()
                    if self.at(TokenKind.PUNCT, "(")
                    else self.parse_identifier()
                )
            else:
                self.advance()
        self.expect_keyword("ON")
        table = self.parse_qualified_name()
        if self.accept_keyword("COLUMN"):
            columns.append(
                self._parse_identifier_list_in_parens()
                if self.at(TokenKind.PUNCT, "(")
                else [self.parse_identifier()]
            )
        return ast.DialectStatement(
            "collect_statistics",
            {"table": table, "columns": columns, "indexes": indexes, "using": using},
            [ast.RefHint(table, "reads")],
            span=self._token_span(start),
        )

    def parse_update(self) -> ast.Update:
        start = self.advance()
        target = self.parse_qualified_name()
        alias = self._parse_optional_alias()
        from_items = self._parse_from_list() if self.accept_keyword("FROM") else []
        self.expect_keyword("SET")
        assignments = self.parse_comma_list(self._parse_assignment)
        where = self.parse_expr() if self.accept_keyword("WHERE") else None
        return ast.Update(
            target, alias, assignments, from_items, where, span=self._token_span(start)
        )

    def parse_insert(self) -> ast.Insert:
        start = self.advance()
        overwrite = bool(self.accept_keyword("OVERWRITE"))
        self.accept_keyword("INTO") or self.accept_keyword("TABLE")
        target = self.parse_qualified_name()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        partition = []
        if self.accept_keyword("PARTITION") and self.accept(TokenKind.PUNCT, "("):
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

    def parse_delete(self) -> ast.Delete:
        start = self.advance()
        self.accept_keyword("FROM")
        target = self.parse_qualified_name()
        alias = self._parse_optional_alias()
        using = []
        if self.accept_keyword("FROM"):
            using = self._parse_from_list()
        elif self.accept_keyword("USING"):
            using = self._parse_from_list()
        if self.accept_keyword("ALL"):
            pass
        where = self.parse_expr() if self.accept_keyword("WHERE") else None
        return ast.Delete(target, alias, using, where, span=self._token_span(start))

    def parse_grant(self) -> ast.Grant:
        start = self.expect_keyword("GRANT")
        role_start = self.index
        role_name = self.parse_qualified_name()
        if self.accept_keyword("TO"):
            grantees = self.parse_comma_list(self._parse_td_grantee)
            return ast.Grant(
                ["ROLE"],
                "role",
                role_name,
                grantees,
                span=self._token_span(start),
            )
        self.index = role_start
        privileges = self._parse_td_privileges()
        object_kind = "object"
        object_name = ast.QualifiedName()
        if self.accept_keyword("ON"):
            if self.at_keyword(
                "TABLE", "VIEW", "DATABASE", "PROCEDURE", "FUNCTION", "MACRO", "ROLE"
            ):
                object_kind = self.advance().text.casefold()
            object_name = self.parse_qualified_name()
        self.expect_keyword("TO")
        grantees = self.parse_comma_list(self._parse_td_grantee)
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
        privileges = self._parse_td_privileges()
        self.expect_keyword("ON")
        object_kind = "object"
        if self.at_keyword("TABLE", "VIEW", "DATABASE", "PROCEDURE", "FUNCTION", "MACRO", "ROLE"):
            object_kind = self.advance().text.casefold()
        object_name = self.parse_qualified_name()
        self.expect_keyword("FROM")
        grantees = self.parse_comma_list(self._parse_td_grantee)
        return ast.Revoke(privileges, object_kind, object_name, grantees, grant_option)

    def _parse_td_privileges(self) -> list[str]:
        privileges = []
        while True:
            first = self.advance().text
            privilege = first
            if first.casefold() in {"create", "drop", "alter", "execute"} and self.at_keyword(
                "table", "view", "database", "user", "role", "procedure", "function", "macro"
            ):
                privilege = f"{first} {self.advance().text}"
            elif first.casefold() == "all":
                self.accept_keyword("PRIVILEGES")
            privileges.append(privilege)
            if not self.accept(TokenKind.PUNCT, ","):
                return privileges

    def _parse_td_grantee(self) -> ast.QualifiedName:
        if self.at_keyword("ROLE", "USER", "GROUP"):
            self.advance()
        return self.parse_qualified_name()

    def parse_set(self) -> ast.Node:
        start = self.expect_keyword("SET")
        if self.accept_keyword("SCHEMA", "DATABASE"):
            target = self.parse_qualified_name()
            self.default_schema = ".".join(part.value for part in target.parts)
            return ast.Use(database=target, span=self._token_span(start))
        self.index -= 1
        return super().parse_set()

    def _can_be_alias(self) -> bool:
        return super()._can_be_alias() and self.peek().text.upper() not in {
            "SAMPLE",
            "QUALIFY",
            "WITH",
            "PRIMARY",
            "UNIQUE",
            "INDEX",
            "PARTITION",
            "MOD",
            "ALL",
        }

    def _literal_or_expr(self) -> ast.Node:
        token = self.peek()
        if token.kind is TokenKind.STRING:
            self.advance()
            return ast.Literal(type="string", value=token.value, span=self._token_span(token))
        return self.parse_expr(80)

    def _redact_option(self, options: dict[str, Any], key: str) -> None:
        if key in options:
            options[key] = "<redacted>"

    def _index_for_token(self, token: Token) -> int:
        for index, candidate in enumerate(self.tokens):
            if candidate is token:
                return index
        return self.index


__all__ = ["Parser"]
