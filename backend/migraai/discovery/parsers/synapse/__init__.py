from __future__ import annotations

from typing import Any

from migraai.discovery import ast
from migraai.discovery.lexer import Token, TokenKind
from migraai.discovery.parser_base import ParseError
from migraai.discovery.parsers.sqlserver import TSqlParserBase


class Parser(TSqlParserBase):
    builtin_functions = TSqlParserBase.builtin_functions | {
        "APPROX_COUNT_DISTINCT",
        "CHECKSUM_AGG",
        "DISTRIBUTION_ID",
        "IDENTITY",
        "MAXDOP",
        "MIN_ACTIVE_ROWVERSION",
        "SESSION_CONTEXT",
        "STRING_ESCAPE",
    }

    def parse_statement_ext(self) -> ast.Node | None:
        if self.at_keyword("COPY"):
            return self._parse_copy_into()
        if self.at_keyword("RENAME"):
            return self._parse_rename_object()
        if self.at_keyword("UPDATE") and (
            self.at_keyword("STATISTICS", 1) or self.at_keyword("STATS", 1)
        ):
            return self._parse_update_statistics()
        return super().parse_statement_ext()

    def parse_create(self) -> ast.Node:
        start_index = self.index
        start = self.expect_keyword("CREATE")
        if self.accept_keyword("DATABASE"):
            if self.accept_keyword("SCOPED"):
                self.expect_keyword("CREDENTIAL")
                return self._parse_credential(start)
            self.index = start_index
            return super().parse_create()
        if self.accept_keyword("MASTER"):
            self.expect_keyword("KEY")
            options: dict[str, ast.Node] = {}
            if self.accept_keyword("ENCRYPTION"):
                self.expect_keyword("BY")
                self.expect_keyword("PASSWORD")
                self.expect(TokenKind.OPERATOR, "=")
                self._parse_synapse_value("password")
                options["password"] = ast.Literal("string", "***")
            return ast.DialectStatement(
                name="create_master_key",
                args={
                    "object_type": "master_key",
                    "name": ast.QualifiedName(parts=[ast.Identifier(value="master_key")]),
                    "options": self._redact_options(options),
                },
                span=self._token_span(start),
            )
        if self.accept_keyword("EXTERNAL"):
            if self.accept_keyword("DATA"):
                self.expect_keyword("SOURCE")
                return self._parse_external_object(start, "external_data_source")
            if self.accept_keyword("FILE"):
                self.expect_keyword("FORMAT")
                return self._parse_external_object(start, "external_file_format")
            if self.accept_keyword("TABLE"):
                return self._parse_synapse_table(start, external=True)
            raise ParseError("Unsupported CREATE EXTERNAL statement.", self.peek())
        if self.accept_keyword("WORKLOAD"):
            kind = "workload_group" if self.accept_keyword("GROUP") else "workload_classifier"
            if kind == "workload_classifier":
                self.expect_keyword("CLASSIFIER")
            return self._parse_workload_object(start, kind)
        if self.accept_keyword("MATERIALIZED"):
            self.expect_keyword("VIEW")
            return self._parse_materialized_view(start)
        if self.accept_keyword("TABLE"):
            return self._parse_synapse_table(start, external=False)
        if self.accept_keyword("STATISTICS"):
            return self._parse_create_statistics(start)
        self.index = start_index
        return super().parse_create()

    def _parse_synapse_table(self, start: Token, *, external: bool) -> ast.CreateTable:
        name = self.parse_qualified_name()
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
        options = self._parse_synapse_options() if self.accept_keyword("WITH") else {}
        as_query = self.parse_query() if self.accept_keyword("AS") else None
        if as_query is not None:
            body = as_query.body
            if isinstance(body, ast.Select):
                for hint in body.hints:
                    if hint.casefold().startswith("label"):
                        options["label"] = ast.Literal(
                            "string", hint.split("=", 1)[-1].strip(" '\"")
                        )
        return ast.CreateTable(
            name=name,
            columns=columns,
            constraints=constraints,
            as_query=as_query,
            external=external,
            options=options,
            span=self._token_span(start),
        )

    def _parse_synapse_options(self, *, optional_parentheses: bool = False) -> dict[str, Any]:
        options: dict[str, Any] = {}
        opening = (
            self.accept(TokenKind.PUNCT, "(")
            if optional_parentheses
            else self.expect(TokenKind.PUNCT, "(")
        )
        while (
            not (opening is not None and self.at(TokenKind.PUNCT, ")"))
            and not self.at(TokenKind.EOF)
            and not (opening is None and self.at(TokenKind.TERMINATOR))
        ):
            if self.at_keyword("CLUSTERED"):
                self.advance()
                if self.accept_keyword("COLUMNSTORE"):
                    self.expect_keyword("INDEX")
                    index_type = "clustered_columnstore"
                    columns = self._parse_synapse_order()
                else:
                    self.expect_keyword("INDEX")
                    index_type = "clustered"
                    columns = self._parse_synapse_column_group()
                options["index"] = ast.DialectExpr(
                    name="table_index",
                    args={"type": index_type, "columns": columns},
                )
            elif self.accept_keyword("HEAP"):
                options["index"] = ast.DialectExpr(
                    name="table_index", args={"type": "heap", "columns": []}
                )
            elif self.accept_keyword("DISTRIBUTION"):
                self.expect(TokenKind.OPERATOR, "=")
                options["distribution"] = self._parse_distribution()
            elif self.accept_keyword("PARTITION"):
                options["partition"] = self._parse_partition()
            else:
                key = self.advance().text.casefold()
                if key == "format_options":
                    options[key] = self._parse_synapse_options()
                elif self.accept(TokenKind.OPERATOR, "="):
                    if key == "credential" and self.at(TokenKind.PUNCT, "("):
                        options[key] = self._parse_synapse_options()
                    else:
                        options[key] = self._parse_synapse_value(key)
                else:
                    options[key] = ast.Literal("boolean", "true")
            if not self.accept(TokenKind.PUNCT, ","):
                break
        if opening is not None:
            self.expect(TokenKind.PUNCT, ")")
        return self._redact_options(options)

    def _parse_distribution(self) -> ast.DialectExpr:
        if self.accept_keyword("HASH"):
            columns = self._parse_synapse_column_group()
            return ast.DialectExpr(name="distribution", args={"type": "hash", "columns": columns})
        if self.accept_keyword("ROUND_ROBIN"):
            return ast.DialectExpr(name="distribution", args={"type": "round_robin"})
        if self.accept_keyword("REPLICATE"):
            return ast.DialectExpr(name="distribution", args={"type": "replicate"})
        raise ParseError("Expected HASH, ROUND_ROBIN, or REPLICATE.", self.peek())

    def _parse_partition(self) -> ast.DialectExpr:
        self.expect(TokenKind.PUNCT, "(")
        column = self.parse_identifier()
        self.expect_keyword("RANGE")
        direction = "right" if self.accept_keyword("RIGHT") else "left"
        if direction == "left":
            self.accept_keyword("LEFT")
        self.expect_keyword("FOR")
        self.expect_keyword("VALUES")
        self.expect(TokenKind.PUNCT, "(")
        values = self.parse_comma_list(self.parse_expr)
        self.expect(TokenKind.PUNCT, ")")
        self.expect(TokenKind.PUNCT, ")")
        return ast.DialectExpr(
            name="partition",
            args={"column": column, "range": direction, "values": values},
        )

    def _parse_synapse_order(self) -> list[ast.Node]:
        if self.accept_keyword("ORDER"):
            return self._parse_synapse_column_group()
        return []

    def _parse_synapse_column_group(self) -> list[ast.Node]:
        self.expect(TokenKind.PUNCT, "(")
        columns = self.parse_comma_list(self.parse_identifier)
        self.expect(TokenKind.PUNCT, ")")
        return columns

    def _parse_synapse_value(self, key: str = "") -> ast.Node:
        token = self.peek()
        if token.kind in {TokenKind.STRING, TokenKind.NATIONAL_STRING}:
            self.advance()
            return ast.Literal("string", token.value, span=self._token_span(token))
        if token.kind is TokenKind.NUMBER:
            self.advance()
            return ast.Literal("number", token.value, span=self._token_span(token))
        if token.kind is TokenKind.PARAM:
            return self.parse_expr()
        if self.accept(TokenKind.PUNCT, "("):
            pairs: dict[str, ast.Node] = {}
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                option = self.advance().text.casefold()
                if option == "secret" or option == "password":
                    self.accept(TokenKind.OPERATOR, "=")
                    if self.at(TokenKind.STRING):
                        self.advance()
                    pairs[option] = ast.Literal("string", "***")
                else:
                    self.expect(TokenKind.OPERATOR, "=")
                    pairs[option] = self._parse_synapse_value(option)
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
            return ast.DialectExpr(name="options", args=pairs)
        name = self.parse_qualified_name()
        if key in {"data_source", "file_format", "credential"}:
            return name
        return ast.ColumnRef(name)

    def _parse_external_object(self, start: Token, object_type: str) -> ast.DialectStatement:
        name = self.parse_qualified_name()
        options = self._parse_synapse_options() if self.accept_keyword("WITH") else {}
        return ast.DialectStatement(
            name=f"create_{object_type}",
            args={"object_type": object_type, "name": name, "options": options},
            span=self._token_span(start),
        )

    def _parse_credential(self, start: Token) -> ast.DialectStatement:
        name = self.parse_qualified_name()
        options = (
            self._parse_synapse_options(optional_parentheses=True)
            if self.accept_keyword("WITH")
            else {}
        )
        return ast.DialectStatement(
            name="create_credential",
            args={
                "object_type": "credential",
                "name": name,
                "options": self._redact_options(options),
            },
            span=self._token_span(start),
        )

    def _parse_workload_object(self, start: Token, object_type: str) -> ast.DialectStatement:
        name = self.parse_qualified_name()
        options = self._parse_synapse_options() if self.accept_keyword("WITH") else {}
        return ast.DialectStatement(
            name=f"create_{object_type}",
            args={"object_type": object_type, "name": name, "options": options},
            span=self._token_span(start),
        )

    def _parse_materialized_view(self, start: Token) -> ast.CreateView:
        name = self.parse_qualified_name()
        options = self._parse_synapse_options() if self.accept_keyword("WITH") else {}
        self.expect_keyword("AS")
        return ast.CreateView(
            name=name,
            query=self.parse_query(),
            materialized=True,
            options=options,
            span=self._token_span(start),
        )

    def _parse_create_statistics(self, start: Token) -> ast.DialectStatement:
        name = self.parse_qualified_name()
        self.expect_keyword("ON")
        table = self.parse_qualified_name()
        columns = self._parse_identifier_list_in_parens()
        return ast.DialectStatement(
            name="create_statistics",
            args={"object_type": "statistics", "name": name, "table": table, "columns": columns},
            references=[ast.RefHint(table, "reads")],
            span=self._token_span(start),
        )

    def _parse_update_statistics(self) -> ast.DialectStatement:
        start = self.expect_keyword("UPDATE")
        self.accept_keyword("STATISTICS") or self.expect_keyword("STATS")
        table = self.parse_qualified_name()
        columns = self._parse_identifier_list_in_parens() if self.at(TokenKind.PUNCT, "(") else []
        options: dict[str, ast.Node] = {}
        if self.accept_keyword("WITH"):
            while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR):
                value = self.advance()
                options[value.text.casefold()] = ast.ColumnRef(
                    ast.QualifiedName(parts=[ast.Identifier(value=value.text)])
                )
                if not self.accept(TokenKind.PUNCT, ","):
                    break
        return ast.DialectStatement(
            name="update_statistics",
            args={"table": table, "columns": columns, "options": options},
            references=[ast.RefHint(table, "reads")],
            span=self._token_span(start),
        )

    def _parse_copy_into(self) -> ast.DialectStatement:
        start = self.expect_keyword("COPY")
        self.expect_keyword("INTO")
        target = self.parse_qualified_name()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        self.expect_keyword("FROM")
        sources: list[ast.Node] = []
        while True:
            sources.append(self._parse_synapse_value())
            if not self.accept(TokenKind.PUNCT, ","):
                break
        options = self._parse_synapse_options() if self.accept_keyword("WITH") else {}
        return ast.DialectStatement(
            name="copy_into",
            args={
                "target": target,
                "columns": columns,
                "sources": sources,
                "options": self._redact_options(options),
            },
            references=[ast.RefHint(target, "writes")],
            span=self._token_span(start),
        )

    def _parse_rename_object(self) -> ast.Rename:
        start = self.expect_keyword("RENAME")
        object_kind = self.advance().text.casefold()
        if self.accept(TokenKind.OPERATOR, "::"):
            pass
        name = self.parse_qualified_name()
        self.expect_keyword("TO")
        new_name = self.parse_qualified_name()
        return ast.Rename(
            object_kind=object_kind,
            name=name,
            new_name=new_name,
            span=self._token_span(start),
        )

    def _redact_options(self, options: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in options.items():
            if key.casefold() in {"secret", "password"}:
                result[key] = ast.Literal("string", "***")
            elif isinstance(value, ast.DialectExpr) and value.name == "options":
                result[key] = ast.DialectExpr(
                    name="options",
                    args=self._redact_options(value.args),
                )
            else:
                result[key] = value
        return result


__all__ = ["Parser"]
