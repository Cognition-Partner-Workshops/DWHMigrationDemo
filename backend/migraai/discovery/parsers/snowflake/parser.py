from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from migraai.discovery import ast
from migraai.discovery.lexer import Token, TokenKind
from migraai.discovery.parser_base import ParseError, ParserBase
from migraai.discovery.parsers.snowflake.lexer import SnowflakeLexer


@dataclass(slots=True)
class SnowflakeGrant(ast.Grant):
    grantee_kinds: list[str] = field(default_factory=list)


SnowflakeGrant.kind = "grant"


@dataclass(slots=True)
class SnowflakeRevoke(ast.Revoke):
    grantee_kinds: list[str] = field(default_factory=list)


SnowflakeRevoke.kind = "revoke"


class Parser(ParserBase):
    case_rule = "upper"
    default_schema: str | None = None
    builtin_functions = ParserBase.ansi_builtins | frozenset(
        {
            "ARRAY_CONSTRUCT",
            "ARRAY_SIZE",
            "ARRAY_TO_STRING",
            "AS_CHAR",
            "AS_VARCHAR",
            "BITAND",
            "BITOR",
            "BITXOR",
            "CONVERT_TIMEZONE",
            "DATEADD",
            "DATEDIFF",
            "DATE_PART",
            "DATE_TRUNC",
            "FLATTEN",
            "GET",
            "GET_PATH",
            "IFF",
            "IFNULL",
            "INFORMATION_SCHEMA",
            "IS_ARRAY",
            "IS_OBJECT",
            "IS_VARCHAR",
            "LAST_DAY",
            "LISTAGG",
            "OBJECT_CONSTRUCT",
            "OBJECT_KEYS",
            "PARSE_JSON",
            "REGEXP_REPLACE",
            "SPLIT",
            "SPLIT_PART",
            "SYSTEM$STREAM_HAS_DATA",
            "TO_BOOLEAN",
            "TO_CHAR",
            "TO_DATE",
            "TO_NUMBER",
            "TO_TIMESTAMP",
            "TRY_PARSE_JSON",
            "TRY_TO_DATE",
            "TRY_TO_NUMBER",
            "TYPEOF",
            "UUID_STRING",
            "ZEROIFNULL",
            "IDENTIFIER",
            "NVL",
            "NVL2",
            "GREATEST",
            "LEAST",
            "HASH",
            "CURRENT_DATABASE",
            "CURRENT_ROLE",
            "CURRENT_SCHEMA",
            "CURRENT_WAREHOUSE",
        }
    )

    def __init__(self, text: str | None = None, rel_path: str = "") -> None:
        super().__init__(text, rel_path)
        self.default_schema = None
        self._lexer_issues: list[ast.Issue] = []

    def parse(self, text: str | None = None, rel_path: str | None = None) -> ast.ParseResult:
        if text is not None:
            self.text = text
            self.default_schema = None
            lexer = SnowflakeLexer(text)
            self.tokens = lexer.tokenize()
            self._lexer_issues = list(lexer.issues)
        result = super().parse(None, rel_path)
        result.issues[:0] = self._lexer_issues
        self.issues = result.issues
        for statement in result.statements:
            if isinstance(statement, ast.Use):
                self._set_default_schema(statement.database)
        return result

    def _set_default_schema(self, name: ast.QualifiedName) -> None:
        if not name.parts:
            return
        parts = [part.value for part in name.parts]
        if len(parts) > 1:
            self.default_schema = ".".join(parts[-2:])
        elif self.default_schema:
            self.default_schema = f"{self.default_schema.split('.', 1)[0]}.{parts[0]}"
        else:
            self.default_schema = parts[0]

    def parse_qualified_name(self) -> ast.QualifiedName:
        if self.at(TokenKind.OPERATOR, ":"):
            start = self.advance()
            token = self.advance()
            if token.kind not in {TokenKind.IDENT, TokenKind.QUOTED_IDENT}:
                raise ParseError("Expected bind variable.", token)
            identifier = ast.Identifier(
                value=f":{token.value}", span=self._range_span(start, token)
            )
            return ast.QualifiedName(parts=[identifier], span=identifier.span)
        if self.at(TokenKind.PARAM):
            token = self.advance()
            return ast.QualifiedName(
                parts=[ast.Identifier(value=token.text, span=self._token_span(token))],
                span=self._token_span(token),
            )
        if self.at_keyword("IDENTIFIER") and self.peek(1).text == "(":
            start = self.advance()
            self.advance()
            argument = self.parse_expr()
            self.expect(TokenKind.PUNCT, ")")
            if isinstance(argument, ast.Literal) and argument.type == "string":
                parts = self._split_identifier(argument.value)
                return ast.QualifiedName(parts=parts, span=self._token_span(start))
            value = (
                argument.name
                if isinstance(argument, ast.Variable)
                else argument.text
                if isinstance(argument, ast.Param)
                else ""
            )
            self.issues.append(
                ast.Issue(
                    "info",
                    "snowflake.dynamic_identifier",
                    "IDENTIFIER() uses a dynamic name that cannot be resolved statically.",
                    self._token_span(start),
                )
            )
            return ast.QualifiedName(
                parts=[ast.Identifier(value=value, span=self._token_span(start))],
                span=self._token_span(start),
            )
        return super().parse_qualified_name()

    @staticmethod
    def _split_identifier(value: str) -> list[ast.Identifier]:
        parts: list[ast.Identifier] = []
        current: list[str] = []
        quoted = False
        index = 0
        while index < len(value):
            char = value[index]
            if char == '"':
                if quoted and index + 1 < len(value) and value[index + 1] == '"':
                    current.append('"')
                    index += 1
                else:
                    quoted = not quoted
            elif char == "." and not quoted:
                parts.append(ast.Identifier(value="".join(current), quoted=quoted))
                current = []
            else:
                current.append(char)
            index += 1
        if current or value.endswith("."):
            parts.append(ast.Identifier(value="".join(current), quoted=quoted))
        return parts

    def parse_primary_ext(self) -> ast.Node | None:
        if self.at(TokenKind.OPERATOR, ":"):
            token = self.advance()
            name = self.advance()
            if name.kind not in {TokenKind.IDENT, TokenKind.QUOTED_IDENT}:
                raise ParseError("Expected Snowflake bind variable.", name)
            return ast.Variable(name.value, span=self._range_span(token, name))
        return None

    def parse_postfix_ext(self, left: ast.Node) -> ast.Node | None:
        if self.accept(TokenKind.OPERATOR, ":"):
            key = self.parse_identifier()
            return ast.DialectExpr("json_path", {"expr": left, "key": key})
        if self.accept(TokenKind.OPERATOR, "::"):
            datatype = self.parse_data_type()
            return ast.Cast(left, datatype, "double_colon")
        return None

    def _can_be_alias(self) -> bool:
        if self.at_keyword(
            "AT",
            "BEFORE",
            "LATERAL",
            "DISTRIBUTE",
            "SORT",
            "CLUSTER",
            "FILE_FORMAT",
            "PATTERN",
            "ON_ERROR",
            "PURGE",
        ):
            return False
        return super()._can_be_alias()

    def _parse_from_item(self) -> ast.Node:
        lateral = bool(self.accept_keyword("LATERAL"))
        start = self.peek()
        if self.accept_keyword("FLATTEN"):
            call = self._parse_function_after_name(ast.QualifiedName([ast.Identifier("FLATTEN")]))
            alias = self._parse_optional_alias()
            return ast.TableFunctionRef(call, alias, lateral, span=self._token_span(start))
        if self.accept_keyword("TABLE"):
            self.expect(TokenKind.PUNCT, "(")
            expression = self.parse_expr()
            self.expect(TokenKind.PUNCT, ")")
            call = expression if isinstance(expression, ast.FunctionCall) else None
            alias = self._parse_optional_alias()
            return ast.TableFunctionRef(call, alias, lateral, span=self._token_span(start))
        if self.accept(TokenKind.PUNCT, "("):
            query = self.parse_query()
            self.expect(TokenKind.PUNCT, ")")
            return ast.SubqueryRef(query, self._parse_optional_alias(), lateral)
        name = self.parse_qualified_name()
        stage_ref = None
        if name.parts and name.parts[0].value.startswith("@"):
            stage_ref = self._stage_location(name.parts[0].value)
            name = stage_ref.args["name"]
        if self.at(TokenKind.PUNCT, "("):
            call = self._parse_function_after_name(name)
            return ast.TableFunctionRef(call, self._parse_optional_alias(), lateral)
        temporal = self._parse_temporal_clause()
        alias = self._parse_optional_alias()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        return ast.TableRef(
            name,
            alias,
            columns,
            temporal=temporal,
            hints=["stage"] if stage_ref is not None else [],
            span=self._token_span(start),
        )

    def _stage_location(self, location: str) -> ast.DialectExpr:
        value = location[1:]
        stage_kind = "named"
        if value.startswith("%"):
            value = value[1:]
            stage_kind = "table"
        elif value == "~" or value.startswith("~/"):
            stage_kind = "user"
        name_text, separator, path = value.partition("/")
        if stage_kind == "user":
            name = ast.QualifiedName(parts=[ast.Identifier(value=f"@{name_text}")])
        else:
            name = ast.QualifiedName(parts=self._split_identifier(name_text))
        return ast.DialectExpr(
            "stage_ref",
            {
                "name": name,
                "path": path if separator else "",
                "stage_kind": stage_kind,
            },
        )

    def _parse_temporal_clause(self) -> ast.DialectExpr | None:
        mode = "at"
        if self.accept_keyword("AT"):
            mode = "at"
        elif self.accept_keyword("BEFORE"):
            mode = "before"
        else:
            return None
        self.expect(TokenKind.PUNCT, "(")
        kind_token = self.advance()
        kind = kind_token.text.casefold()
        self.expect(TokenKind.OPERATOR, "=>")
        value = self.parse_expr()
        self.expect(TokenKind.PUNCT, ")")
        return ast.DialectExpr("temporal", {"mode": mode, "kind": kind, "value": value})

    def _parse_function_after_name(self, name: ast.QualifiedName) -> ast.FunctionCall:
        self.expect(TokenKind.PUNCT, "(")
        distinct = bool(self.accept_keyword("DISTINCT"))
        args = (
            self.parse_comma_list(self._parse_function_arg)
            if not self.at(TokenKind.PUNCT, ")")
            else []
        )
        self.expect(TokenKind.PUNCT, ")")
        return ast.FunctionCall(name=name, args=args, distinct=distinct)

    def parse_statement_ext(self) -> ast.Node | None:
        if self.at_keyword("USE") and self.peek(1).text.upper() in {
            "DATABASE",
            "SCHEMA",
            "WAREHOUSE",
            "ROLE",
            "SECONDARY",
        }:
            start = self.advance()
            kind = self.advance().text.casefold()
            if kind in {"database", "schema"}:
                name = self.parse_qualified_name()
                self._set_default_schema(name)
                return ast.Use(name, span=self._range_span(start, self.tokens[self.index - 1]))
            if kind == "secondary":
                self.accept_keyword("ROLES")
                value = self.advance().text.casefold() if self._is_name() else "all"
                return ast.DialectStatement("use_secondary_roles", {"value": value})
            name = self.parse_qualified_name()
            return ast.DialectStatement(f"use_{kind}", {"name": name})
        if self.at_keyword("SET"):
            self.advance()
            if self.at(TokenKind.PUNCT, "("):
                self.advance()
                names = self.parse_comma_list(self.parse_identifier)
                self.expect(TokenKind.PUNCT, ")")
                self.expect(TokenKind.OPERATOR, "=")
                self.expect(TokenKind.PUNCT, "(")
                values = self.parse_comma_list(self.parse_expr)
                self.expect(TokenKind.PUNCT, ")")
                return ast.SetStatement(
                    None,
                    [
                        ast.SetItem(ast.ColumnRef(ast.QualifiedName([name])), value)
                        for name, value in zip(names, values, strict=False)
                    ],
                )
            name = self.parse_expr(70)
            self.expect(TokenKind.OPERATOR, "=")
            return ast.SetStatement(None, [ast.SetItem(name, self.parse_expr())])
        if self.at_keyword("COPY"):
            return self._parse_copy_into()
        if self.at_keyword("DECLARE"):
            self.advance()
            declarations = []
            while not self.at_keyword("BEGIN") and not self.at(TokenKind.EOF):
                declarations.append(self._parse_declare_after_keyword())
                self.accept(TokenKind.TERMINATOR)
            block = self.parse_block()
            block.declarations[:0] = declarations
            return block
        if self.at_keyword("UNSET"):
            self.advance()
            items = [ast.SetItem(ast.ColumnRef(self.parse_qualified_name()), None)]
            while self.accept(TokenKind.PUNCT, ","):
                items.append(ast.SetItem(ast.ColumnRef(self.parse_qualified_name()), None))
            return ast.SetStatement(None, items)
        if self.at_keyword("EXECUTE") and self.peek(1).text.upper() == "IMMEDIATE":
            self.advance()
            self.advance()
            value = self._parse_dynamic_sql_expr()
            using = []
            if self.accept_keyword("USING"):
                self.expect(TokenKind.PUNCT, "(")
                using = self.parse_comma_list(self.parse_expr)
                self.expect(TokenKind.PUNCT, ")")
            parsed = self._parse_embedded_sql(value)
            return ast.DynamicSql(value, using=using, parsed=parsed)
        if self.at_keyword("BEGIN") and self.peek(1).text.upper() in {"TRANSACTION", "WORK"}:
            self.advance()
            self.advance()
            return ast.DialectStatement("begin_transaction", {})
        if self.at_keyword("BEGIN") and self.peek(1).kind is TokenKind.TERMINATOR:
            self.advance()
            return ast.DialectStatement("begin_transaction", {})
        if self.at_keyword("LET"):
            self.advance()
            name = self.parse_identifier()
            datatype = (
                self.parse_data_type()
                if self._is_name() and not self.at_keyword("DEFAULT")
                else None
            )
            if not (
                self.accept(TokenKind.OPERATOR, ":=")
                or self.accept_keyword("DEFAULT")
                or self.accept(TokenKind.OPERATOR, "=")
            ):
                raise ParseError("Expected LET assignment.", self.peek())
            value = self.parse_expr()
            return ast.Declare(name, datatype, value)
        if self.at_keyword("PUT", "LIST", "REMOVE"):
            operation = self.advance().text.casefold()
            args = []
            while not self._statement_end():
                token = self.advance()
                args.append(ast.Literal("string", token.text, span=self._token_span(token)))
            return ast.DialectStatement(operation, {"arguments": args})
        return None

    def _parse_dynamic_sql_expr(self) -> ast.Node:
        if self.at(TokenKind.DOLLAR_BODY):
            token = self.advance()
            return ast.Literal("string", token.value, span=self._token_span(token))
        return self.parse_expr(70)

    def _parse_copy_into(self) -> ast.DialectStatement:
        start = self.expect_keyword("COPY")
        self.expect_keyword("INTO")
        destination = self.parse_qualified_name()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        self.expect_keyword("FROM")
        if self.at(TokenKind.PARAM) and self.peek().text.startswith("@"):
            location = self.parse_qualified_name()
            source_expr = self._stage_location(location.parts[0].value)
            source = source_expr.args["name"]
            direction = "load"
            hints = [ast.RefHint(source, "reads"), ast.RefHint(destination, "writes")]
        elif self.accept(TokenKind.PUNCT, "("):
            source_expr = self.parse_query()
            self.expect(TokenKind.PUNCT, ")")
            direction = "unload" if destination.parts[0].value.startswith("@") else "load"
            hints = [ast.RefHint(destination, "writes")]
        else:
            source_expr = self.parse_qualified_name()
            direction = "unload" if destination.parts[0].value.startswith("@") else "load"
            hints = (
                [ast.RefHint(source_expr, "reads"), ast.RefHint(destination, "writes")]
                if direction == "unload"
                else [ast.RefHint(source_expr, "reads"), ast.RefHint(destination, "writes")]
            )
        properties: dict[str, Any] = {}
        while not self._statement_end():
            if self.accept_keyword("FILES"):
                self.accept(TokenKind.OPERATOR, "=")
                properties["files"] = self._parse_property_value()
            elif self.accept_keyword("PATTERN", "ON_ERROR", "PURGE"):
                key = self.tokens[self.index - 1].text.casefold()
                self.accept(TokenKind.OPERATOR, "=")
                properties[key] = self._parse_property_value()
            elif self.accept_keyword("FILE_FORMAT"):
                self.accept(TokenKind.OPERATOR, "=")
                properties["file_format"] = self._parse_properties()
                format_name = properties["file_format"].get("format_name")
                if isinstance(format_name, ast.QualifiedName):
                    hints.append(ast.RefHint(format_name, "reads"))
            else:
                break
        return ast.DialectStatement(
            "copy_into",
            {
                "direction": direction,
                "target": destination,
                "columns": columns,
                "source": source_expr,
                "properties": properties,
            },
            hints,
            span=self._token_span(start),
        )

    def parse_create(self) -> ast.Node:
        start_index = self.index
        start = self.expect_keyword("CREATE")
        replace_flag = False
        if self.accept_keyword("OR"):
            self.expect_keyword("REPLACE")
            replace_flag = True
        secure = bool(self.accept_keyword("SECURE"))
        temporary = False
        transient = False
        while self.at_keyword("TRANSIENT", "TEMPORARY", "TEMP", "VOLATILE", "LOCAL", "GLOBAL"):
            modifier = self.advance().text.casefold()
            temporary = temporary or modifier in {"temporary", "temp"}
            transient = transient or modifier == "transient"
        if self.at_keyword("MASKING") or self.at_keyword("ROW"):
            return self._parse_policy(start, replace_flag)
        if self.at_keyword("STAGE", "FILE", "PIPE", "STREAM", "TASK", "TAG", "SHARE", "WAREHOUSE"):
            return self._parse_platform_create(start, replace_flag, temporary)
        recursive_view = bool(self.accept_keyword("RECURSIVE"))
        materialized_view = bool(self.accept_keyword("MATERIALIZED"))
        if materialized_view:
            self.expect_keyword("VIEW")
            return self._parse_snowflake_view(
                start, replace_flag, secure, materialized=True, recursive=recursive_view
            )
        if self.accept_keyword("VIEW"):
            return self._parse_snowflake_view(start, replace_flag, secure, recursive=recursive_view)
        if self.at_keyword("DATABASE", "SCHEMA"):
            kind = self.advance().text.casefold()
            self._parse_if_not_exists()
            name = self.parse_qualified_name()
            properties: dict[str, Any] = {}
            if self.accept_keyword("CLONE"):
                properties["clone"] = self.parse_qualified_name()
                if self.at_keyword("AT", "BEFORE"):
                    properties["at"] = self._parse_temporal_clause()
            if self.accept_keyword("WITH"):
                properties.update(self._parse_properties())
            cls = ast.CreateDatabase if kind == "database" else ast.CreateSchema
            return cls(name, properties, span=self._token_span(start))
        if self.at_keyword("TABLE"):
            self.advance()
            node = self._parse_snowflake_table(start, replace_flag, temporary, transient)
            return node
        if self.at_keyword("SEQUENCE"):
            self.advance()
            return self._parse_sequence(start)
        if self.at_keyword("PROCEDURE", "FUNCTION"):
            kind = self.advance().text.casefold()
            return self._parse_snowflake_routine(start, kind, replace_flag, secure, temporary)
        if self.at_keyword("USER", "ROLE"):
            kind = self.advance().text.casefold()
            self._parse_if_not_exists()
            name = self.parse_qualified_name()
            options = self._parse_properties() if self.at(TokenKind.PUNCT, "(") else {}
            return ast.CreatePrincipal(kind, name, options, span=self._token_span(start))
        self.index = start_index
        return super().parse_create()

    def _parse_if_not_exists(self) -> bool:
        if self.accept_keyword("IF"):
            self.expect_keyword("NOT")
            self.expect_keyword("EXISTS")
            return True
        return False

    def _parse_properties(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                key = self.advance().value.casefold()
                self.accept(TokenKind.OPERATOR, "=")
                if self.accept(TokenKind.PUNCT, "("):
                    if self.at(TokenKind.IDENT) and self.peek(1).text == "=":
                        nested = {}
                        while not self.at(TokenKind.PUNCT, ")"):
                            nested_key = self.advance().value.casefold()
                            self.accept(TokenKind.OPERATOR, "=")
                            nested[nested_key] = self._parse_property_value()
                            if not self.accept(TokenKind.PUNCT, ","):
                                break
                        self.expect(TokenKind.PUNCT, ")")
                        value = nested
                    else:
                        value = self.parse_comma_list(self._parse_property_value)
                        self.expect(TokenKind.PUNCT, ")")
                else:
                    value = self._parse_property_value()
                properties[key] = value
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        return properties

    def _parse_property_value(self) -> Any:
        if self.accept(TokenKind.PUNCT, "("):
            if not (self._is_name() and self.peek(1).text == "="):
                values = (
                    self.parse_comma_list(self._parse_property_value)
                    if not self.at(TokenKind.PUNCT, ")")
                    else []
                )
                self.expect(TokenKind.PUNCT, ")")
                return values
            nested = {}
            while not self.at(TokenKind.PUNCT, ")"):
                key = self.advance().value.casefold()
                self.expect(TokenKind.OPERATOR, "=")
                nested[key] = self._parse_property_value()
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
            return nested
        token = self.peek()
        if token.kind is TokenKind.STRING:
            self.advance()
            return ast.Literal("string", token.value, span=self._token_span(token))
        if token.kind is TokenKind.NUMBER:
            self.advance()
            return ast.Literal("number", token.value, span=self._token_span(token))
        if self.accept_keyword("TRUE", "FALSE"):
            return ast.Literal("boolean", token.text.casefold(), span=self._token_span(token))
        if self.accept_keyword("NULL"):
            return ast.Literal("null", "null", span=self._token_span(token))
        if self._is_name():
            return self.parse_qualified_name()
        raise ParseError("Expected property value.", token)

    def parse_data_type(self) -> ast.DataType:
        start = self.peek()
        type_name = start.value.upper() if start.kind is TokenKind.IDENT else ""
        if type_name not in {"ARRAY", "OBJECT", "MAP", "VECTOR", "STRUCT"}:
            return super().parse_data_type()
        name = self.parse_qualified_name()
        children: list[ast.DataType | ast.StructField] = []
        params: list[ast.Node | str] = []
        angle_form = bool(self.accept(TokenKind.OPERATOR, "<"))
        if angle_form or self.accept(TokenKind.PUNCT, "("):
            closing = ">" if angle_form else ")"
            if type_name in {"OBJECT", "STRUCT"}:
                while not self.at(TokenKind.OPERATOR, ">") and not self.at(TokenKind.PUNCT, ")"):
                    field_name = self.parse_identifier()
                    self.accept(TokenKind.OPERATOR, ":")
                    field_type = self.parse_data_type()
                    children.append(ast.StructField(field_name, field_type))
                    if not self.accept(TokenKind.PUNCT, ","):
                        break
            elif type_name == "ARRAY":
                children.append(self.parse_data_type())
            else:
                children.append(self.parse_data_type())
                if self.accept(TokenKind.PUNCT, ","):
                    if type_name == "MAP":
                        children.append(self.parse_data_type())
                    else:
                        params.append(self.parse_expr(80))
            self.expect(
                TokenKind.OPERATOR if angle_form else TokenKind.PUNCT,
                closing,
            )
        return ast.DataType(
            name,
            params,
            children,
            span=self._range_span(start, self.tokens[self.index - 1]),
        )

    def _parse_snowflake_table(
        self, start: Token, replace_flag: bool, temporary: bool, transient: bool
    ) -> ast.CreateTable:
        if_not_exists = self._parse_if_not_exists()
        name = self.parse_qualified_name()
        columns: list[ast.ColumnDef] = []
        constraints: list[ast.TableConstraint] = []
        like = None
        options: dict[str, Any] = {}
        if self.accept_keyword("LIKE"):
            like = self.parse_qualified_name()
        elif self.accept_keyword("CLONE"):
            options["clone"] = self.parse_qualified_name()
            if self.at_keyword("AT", "BEFORE"):
                options["clone_at"] = self._parse_temporal_clause()
        elif self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")"):
                if self._is_constraint_start():
                    constraints.append(self._parse_table_constraint())
                else:
                    col = self._parse_column_def()
                    self._parse_snowflake_column_options(col)
                    columns.append(col)
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        if self.accept_keyword("CLUSTER"):
            self.expect_keyword("BY")
            self.expect(TokenKind.PUNCT, "(")
            options["cluster_by"] = self.parse_comma_list(self.parse_expr)
            self.expect(TokenKind.PUNCT, ")")
        if self.accept_keyword("COMMENT"):
            self.accept(TokenKind.OPERATOR, "=")
            token = self.expect(TokenKind.STRING)
            options["comment"] = ast.Literal("string", token.value, span=self._token_span(token))
        while self.at(TokenKind.IDENT) and self.peek(1).text == "=":
            key = self.advance().value.casefold()
            self.advance()
            options.setdefault("properties", {})[key] = self._parse_property_value()
        as_query = self.parse_query() if self.accept_keyword("AS") else None
        if transient:
            options["transient"] = True
        return ast.CreateTable(
            name,
            columns,
            constraints,
            as_query,
            like,
            temporary=temporary,
            replace=replace_flag,
            if_not_exists=if_not_exists,
            options=options,
            span=self._token_span(start),
        )

    def _parse_snowflake_column_options(self, column: ast.ColumnDef) -> None:
        while (
            not self._statement_end()
            and not self.at(TokenKind.PUNCT, ",")
            and not self.at(TokenKind.PUNCT, ")")
        ):
            if self.accept_keyword("AUTOINCREMENT", "IDENTITY"):
                start_value = increment = None
                order = None
                if self.accept(TokenKind.PUNCT, "("):
                    start_value = self.parse_expr(80)
                    if self.accept(TokenKind.PUNCT, ","):
                        increment = self.parse_expr(80)
                    self.expect(TokenKind.PUNCT, ")")
                if self.accept_keyword("START"):
                    self.accept_keyword("WITH")
                    start_value = self.parse_expr(80)
                if self.accept_keyword("INCREMENT"):
                    self.accept_keyword("BY")
                    increment = self.parse_expr(80)
                if self.accept_keyword("ORDER"):
                    order = "order"
                elif self.accept_keyword("NOORDER"):
                    order = "noorder"
                column.identity = ast.DialectExpr(
                    "identity",
                    {"start": start_value, "increment": increment, "order": order},
                )
            elif self.accept_keyword("DEFAULT"):
                column.default = self.parse_expr()
            elif self.accept_keyword("COLLATE"):
                token = self.expect(TokenKind.STRING)
                column.collation = ast.QualifiedName(parts=self._split_identifier(token.value))
            elif self.accept_keyword("COMMENT"):
                self.accept(TokenKind.OPERATOR, "=")
                column.comment = self.expect(TokenKind.STRING).value
            elif (self.accept_keyword("WITH") and self.at_keyword("MASKING")) or self.at_keyword(
                "MASKING"
            ):
                self.accept_keyword("MASKING")
                self.expect_keyword("POLICY")
                column.options["masking_policy"] = self.parse_qualified_name()
                if self.accept_keyword("USING"):
                    self.expect(TokenKind.PUNCT, "(")
                    column.options["masking_using"] = self.parse_comma_list(self.parse_identifier)
                    self.expect(TokenKind.PUNCT, ")")
            elif (self.accept_keyword("WITH") and self.at_keyword("TAG")) or self.at_keyword("TAG"):
                self.accept_keyword("TAG")
                self.expect(TokenKind.PUNCT, "(")
                tags = {}
                while not self.at(TokenKind.PUNCT, ")"):
                    key = self.parse_qualified_name()
                    self.expect(TokenKind.OPERATOR, "=")
                    tags[".".join(part.value for part in key.parts)] = self._parse_property_value()
                    if not self.accept(TokenKind.PUNCT, ","):
                        break
                self.expect(TokenKind.PUNCT, ")")
                column.options["tags"] = tags
            elif self.accept_keyword("NOT"):
                self.expect_keyword("NULL")
                column.constraints.append(ast.ColumnConstraint("not_null"))
            elif self.accept_keyword("PRIMARY"):
                self.expect_keyword("KEY")
                column.constraints.append(ast.ColumnConstraint("primary_key"))
            elif self.accept_keyword("UNIQUE"):
                column.constraints.append(ast.ColumnConstraint("unique"))
            else:
                break

    def _parse_snowflake_view(
        self,
        start: Token,
        replace_flag: bool,
        secure: bool,
        *,
        materialized: bool = False,
        recursive: bool = False,
    ) -> ast.CreateView:
        if_not_exists = self._parse_if_not_exists()
        name = self.parse_qualified_name()
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        options: dict[str, Any] = {}
        if self.accept_keyword("COPY"):
            self.expect_keyword("GRANTS")
            options["copy_grants"] = True
        while not self.at_keyword("AS") and not self.at(TokenKind.EOF):
            if self.accept_keyword("COMMENT"):
                self.accept(TokenKind.OPERATOR, "=")
                options["comment"] = self._parse_property_value()
            elif self.accept_keyword("CLUSTER"):
                self.expect_keyword("BY")
                self.expect(TokenKind.PUNCT, "(")
                options["cluster_by"] = self.parse_comma_list(self.parse_expr)
                self.expect(TokenKind.PUNCT, ")")
            elif self.accept_keyword("WITH"):
                self.accept_keyword("ROW")
                self.accept_keyword("ACCESS")
                self.accept_keyword("POLICY")
                options["row_access_policy"] = self.parse_qualified_name()
            else:
                break
        self.expect_keyword("AS")
        query = self.parse_query()
        if recursive:
            options["recursive"] = True
        if if_not_exists:
            options["if_not_exists"] = True
        return ast.CreateView(
            name,
            columns,
            query,
            materialized=materialized,
            replace=replace_flag,
            secure=secure,
            options=options,
            span=self._token_span(start),
        )

    def parse_alter(self) -> ast.Alter:
        start = self.expect_keyword("ALTER")
        object_kind = self.advance().text.casefold()
        name = self.parse_qualified_name()
        actions: list[ast.Node] = []
        if object_kind == "task" and self.accept_keyword("RESUME", "SUSPEND"):
            actions.append(
                ast.DialectStatement(
                    self.tokens[self.index - 1].text.casefold(),
                    {},
                )
            )
        elif object_kind == "share" and self.accept_keyword("ADD"):
            self.accept_keyword("ACCOUNTS")
            self.accept(TokenKind.OPERATOR, "=")
            accounts = self._parse_property_value()
            actions.append(ast.DialectStatement("add_accounts", {"accounts": accounts}))
        elif object_kind == "table" and self.accept_keyword("MODIFY"):
            self.accept_keyword("COLUMN")
            column = self.parse_identifier()
            if self.accept_keyword("SET"):
                if self.accept_keyword("MASKING"):
                    self.expect_keyword("POLICY")
                    policy = self.parse_qualified_name()
                    actions.append(
                        ast.DialectStatement(
                            "set_masking_policy",
                            {"column": column, "policy": policy},
                            [ast.RefHint(policy, "reads")],
                        )
                    )
                elif self.accept_keyword("TAG"):
                    actions.append(
                        ast.DialectStatement(
                            "set_tag", {"column": column, "tags": self._parse_property_value()}
                        )
                    )
            else:
                raise ParseError("Expected SET after ALTER TABLE MODIFY COLUMN.", self.peek())
        else:
            actions.append(
                ast.DialectStatement(
                    "action",
                    {"tokens": self._collect_statement_tokens()},
                )
            )
        return ast.Alter(object_kind, name, actions, span=self._token_span(start))

    def _collect_statement_tokens(self) -> list[str]:
        values = []
        while not self._statement_end():
            values.append(self.advance().text)
        return values

    def _parse_sequence(self, start: Token) -> ast.CreateSequence:
        name = self.parse_qualified_name()
        options: dict[str, Any] = {}
        while not self._statement_end():
            if self.accept_keyword("START"):
                self.accept_keyword("WITH") or self.accept(TokenKind.OPERATOR, "=")
                options["start"] = self.parse_expr(80)
            elif self.accept_keyword("INCREMENT"):
                self.accept_keyword("BY") or self.accept(TokenKind.OPERATOR, "=")
                options["increment"] = self.parse_expr(80)
            elif self.accept_keyword("ORDER"):
                options["order"] = True
            elif self.accept_keyword("NOORDER"):
                options["order"] = False
            elif self.accept_keyword("COMMENT"):
                self.accept(TokenKind.OPERATOR, "=")
                options["comment"] = self._parse_property_value()
            else:
                break
        return ast.CreateSequence(name, options, span=self._token_span(start))

    def _parse_platform_create(
        self, start: Token, replace_flag: bool, temporary: bool
    ) -> ast.DialectStatement:
        kind = self.advance().text.casefold()
        if kind == "file":
            self.expect_keyword("FORMAT")
            kind = "file_format"
        elif kind == "warehouse":
            kind = "warehouse"
        elif kind == "stream":
            kind = "stream"
        elif kind == "share":
            kind = "share"
        elif kind == "tag":
            kind = "tag"
        elif kind == "pipe":
            kind = "pipe"
        elif kind == "task":
            kind = "task"
        else:
            kind = "stage"
        if_not_exists = self._parse_if_not_exists()
        name = self.parse_qualified_name()
        args: dict[str, Any] = {
            "object_type": kind,
            "name": name,
            "replace": replace_flag,
            "if_not_exists": if_not_exists,
            "temporary": temporary,
        }
        args["properties"] = {}
        references: list[ast.RefHint] = []
        if kind == "stage":
            self._parse_key_value_properties(args["properties"])
            ff = args["properties"].get("file_format")
            if isinstance(ff, dict):
                format_name = ff.get("format_name")
                if isinstance(format_name, ast.QualifiedName):
                    references.append(ast.RefHint(format_name, "reads"))
        elif kind == "stream":
            if self.accept_keyword("ON"):
                on_kind = self.advance().text.casefold()
                on = self.parse_qualified_name()
                args["on_kind"] = on_kind
                args["on"] = on
                references.append(ast.RefHint(on, "reads"))
        elif kind == "pipe":
            if self.accept_keyword("AUTO_INGEST"):
                self.accept(TokenKind.OPERATOR, "=")
                args["properties"]["auto_ingest"] = self._parse_property_value()
            if self.accept_keyword("AS"):
                args["copy"] = self.parse_statement()
        elif kind == "task":
            self._parse_task_clauses(args, references)
        elif kind == "policy":
            pass
        else:
            self._parse_key_value_properties(args["properties"])
        return ast.DialectStatement(
            f"create_{kind}", args, references, span=self._token_span(start)
        )

    def _parse_key_value_properties(self, properties: dict[str, Any]) -> None:
        while not self._statement_end():
            if self.accept_keyword("WITH"):
                continue
            key = self.advance().value.casefold()
            if self.accept(TokenKind.OPERATOR, "="):
                properties[key] = self._parse_property_value()
            else:
                properties[key] = True
            if not self.accept(TokenKind.PUNCT, ",") and not self._is_name():
                break

    def _parse_task_clauses(self, args: dict[str, Any], references: list[ast.RefHint]) -> None:
        properties = args["properties"]
        if self.accept_keyword("WAREHOUSE"):
            properties["warehouse"] = self.parse_qualified_name()
        if self.accept_keyword("SCHEDULE"):
            self.accept(TokenKind.OPERATOR, "=")
            properties["schedule"] = self._parse_property_value()
        if self.accept_keyword("AFTER"):
            args["after"] = self.parse_comma_list(self.parse_qualified_name)
            references.extend(ast.RefHint(name, "schedules") for name in args["after"])
        if self.accept_keyword("WHEN"):
            args["when"] = self.parse_expr()
            if isinstance(args["when"], ast.FunctionCall) and args["when"].name.parts:
                if args["when"].name.parts[-1].value.casefold() == "system$stream_has_data":
                    arg = args["when"].args[0] if args["when"].args else None
                    if isinstance(arg, ast.Literal):
                        references.append(
                            ast.RefHint(
                                ast.QualifiedName(self._split_identifier(arg.value)), "reads"
                            )
                        )
        if self.accept_keyword("AS"):
            if self.at(TokenKind.DOLLAR_BODY):
                args["body"] = self._parse_embedded_body(self.advance(), procedure=True)
            else:
                args["body"] = self.parse_statement()

    def _parse_policy(self, start: Token, replace_flag: bool) -> ast.DialectStatement:
        row = bool(self.accept_keyword("ROW"))
        if row:
            self.expect_keyword("ACCESS")
            self.expect_keyword("POLICY")
            kind = "create_row_access_policy"
        else:
            self.expect_keyword("MASKING")
            self.expect_keyword("POLICY")
            kind = "create_masking_policy"
        name = self.parse_qualified_name()
        self.expect_keyword("AS")
        self.expect(TokenKind.PUNCT, "(")
        params: list[ast.RoutineParam] = []
        while not self.at(TokenKind.PUNCT, ")"):
            param_name = self.parse_identifier()
            datatype = self.parse_data_type()
            params.append(ast.RoutineParam(param_name, type=datatype))
            if not self.accept(TokenKind.PUNCT, ","):
                break
        self.expect(TokenKind.PUNCT, ")")
        self.expect_keyword("RETURNS")
        returns = self.parse_data_type()
        self.expect(TokenKind.OPERATOR, "->")
        body = self.parse_expr()
        return ast.DialectStatement(
            kind,
            {
                "object_type": "policy",
                "name": name,
                "replace": replace_flag,
                "params": params,
                "returns": returns,
                "body": body,
            },
            span=self._token_span(start),
        )

    def _parse_snowflake_routine(
        self,
        start: Token,
        routine_kind: str,
        replace_flag: bool,
        secure: bool,
        temporary: bool,
    ) -> ast.CreateRoutine:
        name = self.parse_qualified_name()
        params: list[ast.RoutineParam] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")"):
                pname = self.parse_identifier()
                datatype = self.parse_data_type()
                default = self.parse_expr() if self.accept_keyword("DEFAULT") else None
                params.append(ast.RoutineParam(pname, type=datatype, default=default))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        options: dict[str, Any] = {
            "replace": replace_flag,
            "secure": secure,
            "temporary": temporary,
        }
        returns = None
        language = None
        body_token = None
        while not self.at_keyword("AS") and not self.at(TokenKind.EOF):
            if self.accept_keyword("RETURNS"):
                if self.accept_keyword("TABLE"):
                    self.expect(TokenKind.PUNCT, "(")
                    fields = []
                    while not self.at(TokenKind.PUNCT, ")"):
                        fields.append(
                            ast.StructField(self.parse_identifier(), self.parse_data_type())
                        )
                        if not self.accept(TokenKind.PUNCT, ","):
                            break
                    self.expect(TokenKind.PUNCT, ")")
                    returns = ast.DataType("TABLE", children=fields)
                else:
                    returns = self.parse_data_type()
                    self.accept_keyword("NOT")
                    self.accept_keyword("NULL")
            elif self.accept_keyword("LANGUAGE"):
                language = self.advance().text.casefold()
            elif self.accept_keyword("RUNTIME_VERSION"):
                self.accept(TokenKind.OPERATOR, "=")
                options["runtime_version"] = self._parse_property_value()
            elif self.accept_keyword("PACKAGES", "IMPORTS"):
                key = self.tokens[self.index - 1].text.casefold()
                self.accept(TokenKind.OPERATOR, "=")
                options[key] = (
                    self._parse_properties()
                    if self.at(TokenKind.PUNCT, "(")
                    else self._parse_property_value()
                )
            elif self.accept_keyword("HANDLER", "COMMENT"):
                key = self.tokens[self.index - 1].text.casefold()
                self.accept(TokenKind.OPERATOR, "=")
                options[key] = self._parse_property_value()
            elif self.accept_keyword("CALLED"):
                self.expect_keyword("ON")
                self.expect_keyword("NULL")
                self.expect_keyword("INPUT")
                options["null_input"] = True
            elif self.accept_keyword("STRICT", "IMMUTABLE", "VOLATILE"):
                options[self.tokens[self.index - 1].text.casefold()] = True
            elif self.accept_keyword("EXECUTE"):
                self.expect_keyword("AS")
                options["execute_as"] = self.advance().text.casefold()
            elif self.accept_keyword("AS"):
                body_token = self.advance()
                break
            else:
                self.advance()
        if body_token is None and self.accept_keyword("AS"):
            body_token = self.advance()
        body: ast.Node | str | None = None
        if body_token is not None:
            if language in {"javascript", "python", "java", "scala"}:
                body = body_token.value
            else:
                nested = self._parse_embedded_body(
                    body_token, procedure=routine_kind == "procedure"
                )
                if routine_kind == "procedure":
                    body = (
                        nested
                        if isinstance(nested, ast.Block)
                        else ast.Block(statements=[nested] if nested else [])
                    )
                elif isinstance(nested, ast.Query):
                    body = nested
                elif nested is not None:
                    body = nested
        return ast.CreateRoutine(
            routine_kind,
            name,
            params,
            returns,
            language,
            body,
            options,
            span=self._token_span(start),
        )

    def _parse_embedded_body(self, token: Token, procedure: bool = False) -> ast.Node | None:
        text = token.value
        nested_parser = type(self)()
        parsed = nested_parser.parse(text, self.rel_path)
        delimiter = re.match(r"\$[A-Za-z_][\w]*\$|\$\$", token.text)
        opening_len = len(delimiter.group()) if delimiter else 1
        body_line = token.line
        body_col = token.col + opening_len
        line_offset = body_line - 1
        for node in parsed.statements:
            self._shift_spans(node, line_offset, body_col - 1)
        shifted_issues = []
        for issue in parsed.issues:
            if issue.span is not None:
                issue.span = self._shift_span(issue.span, line_offset, body_col - 1)
            shifted_issues.append(issue)
        self.issues.extend(shifted_issues)
        if not parsed.statements:
            return None
        if procedure:
            if len(parsed.statements) == 1 and isinstance(parsed.statements[0], ast.Block):
                return parsed.statements[0]
            return ast.Block(statements=parsed.statements)
        return parsed.statements[0]

    def _shift_spans(self, node: ast.Node, line_delta: int, col_delta: int) -> None:
        for child in ast.walk(node):
            child.span = self._shift_span(child.span, line_delta, col_delta)

    @staticmethod
    def _shift_span(span: ast.Span, line_delta: int, col_delta: int) -> ast.Span:
        return replace(
            span,
            start_line=span.start_line + line_delta,
            start_col=span.start_col + (col_delta if span.start_line == 1 else 0),
            end_line=span.end_line + line_delta,
            end_col=span.end_col + (col_delta if span.end_line == 1 else 0),
        )

    def parse_grant(self) -> ast.Grant:
        start = self.expect_keyword("GRANT")
        if self.accept_keyword("ROLE"):
            privilege_role = self.parse_qualified_name()
            self.expect_keyword("TO")
            grantees, kinds = self._parse_grantees()
            return SnowflakeGrant(
                privileges=["role"],
                object_kind="role",
                object_name=privilege_role,
                grantees=grantees,
                grantee_kinds=kinds,
                span=self._token_span(start),
            )
        privileges = self._parse_privileges()
        object_kind = "role"
        object_name = ast.QualifiedName()
        if self.accept_keyword("ON"):
            object_kind, object_name = self._parse_grant_object()
        self.expect_keyword("TO")
        grantees, kinds = self._parse_grantees()
        with_option = False
        if self.accept_keyword("WITH"):
            self.expect_keyword("GRANT")
            self.expect_keyword("OPTION")
            with_option = True
        node = SnowflakeGrant(
            privileges=privileges,
            object_kind=object_kind,
            object_name=object_name,
            grantees=grantees,
            with_grant_option=with_option,
            grantee_kinds=kinds,
            span=self._token_span(start),
        )
        return node

    def parse_revoke(self) -> ast.Revoke:
        start = self.expect_keyword("REVOKE")
        grant_option = False
        if self.accept_keyword("GRANT"):
            self.expect_keyword("OPTION")
            self.expect_keyword("FOR")
            grant_option = True
        privileges = self._parse_privileges()
        self.expect_keyword("ON")
        object_kind, object_name = self._parse_grant_object()
        self.expect_keyword("FROM")
        grantees, kinds = self._parse_grantees()
        return SnowflakeRevoke(
            privileges,
            object_kind,
            object_name,
            grantees,
            grant_option,
            kinds,
            span=self._token_span(start),
        )

    def _parse_privileges(self) -> list[str]:
        privileges = []
        while True:
            words = []
            while not self.at(TokenKind.EOF) and not self.at(TokenKind.PUNCT, ","):
                if self.at_keyword("ON", "TO", "FROM"):
                    break
                words.append(self.advance().text)
            privileges.append(" ".join(words).casefold())
            if not self.accept(TokenKind.PUNCT, ","):
                break
        return privileges

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
            "COPY",
            "USE",
            "SET",
            "EXECUTE",
            "DECLARE",
            "LET",
            "PUT",
            "LIST",
            "REMOVE",
        }
        previous_line = self.tokens[self.index - 1].line if self.index else 1
        while self.index < len(self.tokens) - 1:
            token = self.tokens[self.index]
            if token.kind is TokenKind.PUNCT and token.text == "(":
                depth += 1
            elif token.kind is TokenKind.PUNCT and token.text == ")":
                depth = max(0, depth - 1)
            elif token.kind is TokenKind.TERMINATOR and depth == 0:
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

    def _parse_grant_object(self) -> tuple[str, ast.QualifiedName]:
        if self.accept_keyword("FUTURE") or self.accept_keyword("ALL"):
            modifier = self.tokens[self.index - 1].text.casefold()
            self.expect_keyword("TABLES")
            self.expect_keyword("IN")
            self.expect_keyword("SCHEMA")
            return f"{modifier}_tables_in_schema", self.parse_qualified_name()
        kind = self.advance().text.casefold()
        if kind in {"function", "procedure"}:
            name = self.parse_qualified_name()
            types = self._parse_argument_types()
            parts = [*name.parts, ast.Identifier(value=f"({','.join(types)})")]
            return kind, ast.QualifiedName(parts=parts)
        return kind, self.parse_qualified_name()

    def _parse_argument_types(self) -> list[str]:
        self.expect(TokenKind.PUNCT, "(")
        types = []
        while not self.at(TokenKind.PUNCT, ")"):
            types.append(self.advance().text)
            while self.accept(TokenKind.PUNCT, "."):
                types[-1] += "." + self.advance().text
            if not self.accept(TokenKind.PUNCT, ","):
                break
        self.expect(TokenKind.PUNCT, ")")
        return types

    def _parse_grantees(self) -> tuple[list[ast.QualifiedName], list[str]]:
        names = []
        kinds = []
        while True:
            if self.accept_keyword("DATABASE"):
                self.expect_keyword("ROLE")
                kinds.append("database_role")
            elif self.accept_keyword("ROLE", "USER", "SHARE"):
                kinds.append(self.tokens[self.index - 1].text.casefold())
            else:
                kinds.append("user")
            names.append(self.parse_qualified_name())
            if not self.accept(TokenKind.PUNCT, ","):
                break
        return names, kinds


__all__ = ["Parser", "SnowflakeGrant", "SnowflakeRevoke"]
