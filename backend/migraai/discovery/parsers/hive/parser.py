from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from migraai.discovery import ast
from migraai.discovery.lexer import Token, TokenKind
from migraai.discovery.parser_base import ParseError, ParserBase
from migraai.discovery.parsers.hive.lexer import LEXER_CONFIG, HiveLexer


@dataclass(slots=True)
class HiveQuery(ast.Query):
    distribute_by: list[ast.Expr] = field(default_factory=list)
    sort_by: list[ast.OrderItem] = field(default_factory=list)
    cluster_by: list[ast.Expr] = field(default_factory=list)


HiveQuery.kind = "query"


@dataclass(slots=True)
class HiveStructField(ast.StructField):
    comment: ast.Literal | None = None


HiveStructField.kind = "struct_field"


class Parser(ParserBase):
    lexer_class = HiveLexer
    lexer_config = LEXER_CONFIG
    case_rule = "lower"
    binary_ops = {
        **ParserBase.binary_ops,
        "==": (30, 31, "="),
        "<=>": (30, 31, "<=>"),
        "DIV": (60, 61, "div"),
        "&": (45, 46, "&"),
        "^": (44, 45, "^"),
        "|": (43, 44, "|"),
    }
    builtin_functions = ParserBase.ansi_builtins | frozenset(
        {
            "ADD_MONTHS",
            "ARRAY",
            "ARRAY_CONTAINS",
            "AVG",
            "COLLECT_LIST",
            "COLLECT_SET",
            "CONCAT_WS",
            "CONV",
            "DATE_ADD",
            "DATE_FORMAT",
            "DATE_SUB",
            "DATEDIFF",
            "EXPLODE",
            "EXTENDED_UNION",
            "FROM_UNIXTIME",
            "GET_JSON_OBJECT",
            "HASH",
            "INLINE",
            "INSTR",
            "ISNULL",
            "JSON_TUPLE",
            "LCASE",
            "LENGTH",
            "MAP",
            "MONTHS_BETWEEN",
            "NAMED_STRUCT",
            "NVL",
            "NVL2",
            "POSEXPLODE",
            "PRINTF",
            "REGEXP_EXTRACT",
            "REGEXP_REPLACE",
            "REPEAT",
            "REVERSE",
            "RPAD",
            "SHA",
            "SHA1",
            "SHA2",
            "SIZE",
            "SPLIT",
            "STACK",
            "STR_TO_MAP",
            "SUBSTR",
            "SUBSTRING_INDEX",
            "TO_DATE",
            "TO_JSON",
            "TO_UTC_TIMESTAMP",
            "TRANSFORM",
            "TRUNC",
            "UCASE",
            "UNIX_TIMESTAMP",
            "UNNEST",
            "UUID",
            "WEEKOFYEAR",
            "YEAR",
            "IF",
            "COALESCE",
        }
    )
    default_schema: str | None = None

    def __init__(self, text: str | None = None, rel_path: str = "") -> None:
        super().__init__(text, rel_path, lexer_config=self.lexer_config)
        self.default_schema = None

    def parse_identifier(self) -> ast.Identifier:
        first = self.peek()
        if first.kind not in {TokenKind.IDENT, TokenKind.QUOTED_IDENT, TokenKind.PARAM}:
            raise ParseError("Expected identifier.", first)
        tokens = [self.advance()]
        while self.peek().kind in {
            TokenKind.IDENT,
            TokenKind.QUOTED_IDENT,
            TokenKind.PARAM,
            TokenKind.NUMBER,
        }:
            previous = tokens[-1]
            current = self.peek()
            if previous.offset + len(previous.text) != current.offset:
                break
            tokens.append(self.advance())
        last = tokens[-1]
        value = "".join(token.value for token in tokens)
        quoted = len(tokens) == 1 and first.kind is TokenKind.QUOTED_IDENT
        return ast.Identifier(value, quoted, span=self._range_span(first, last))

    def _is_name(self) -> bool:
        return self.peek().kind in {
            TokenKind.IDENT,
            TokenKind.QUOTED_IDENT,
            TokenKind.PARAM,
        }

    def _can_be_alias(self) -> bool:
        if not super()._can_be_alias():
            return False
        return self.peek().text.upper() not in {
            "SELECT",
            "DISTRIBUTE",
            "INSERT",
            "LATERAL",
            "SORT",
            "CLUSTER",
        }

    def _parse_prefix(self) -> ast.Node:
        if self.at(TokenKind.OPERATOR, "!"):
            token = self.advance()
            return ast.Unary("not", self.parse_expr(80), span=self._token_span(token))
        return super()._parse_prefix()

    def parse_data_type(self) -> ast.DataType:
        start = self.peek()
        name = self.parse_qualified_name()
        type_word = name.parts[-1].value.casefold() if name.parts else ""
        children: list[ast.DataType | ast.StructField] = []
        params: list[ast.Node | str] = []
        if self.accept(TokenKind.OPERATOR, "<"):
            if type_word == "struct":
                while not self.at(TokenKind.OPERATOR, ">") and not self.at(TokenKind.EOF):
                    field_name = self.parse_identifier()
                    self.expect(TokenKind.OPERATOR, ":")
                    field_type = self.parse_data_type()
                    comment = None
                    if self.accept_keyword("COMMENT"):
                        comment = self._parse_string_literal()
                    children.append(HiveStructField(field_name, field_type, comment=comment))
                    if not self.accept(TokenKind.PUNCT, ","):
                        break
            elif type_word == "uniontype":
                while not self.at(TokenKind.OPERATOR, ">") and not self.at(TokenKind.EOF):
                    children.append(self.parse_data_type())
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

    def parse_query(self) -> HiveQuery:
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
                self.expect(TokenKind.PUNCT, "(")
                query = self.parse_query()
                self.expect(TokenKind.PUNCT, ")")
                ctes.append(ast.Cte(name, columns, query, span=self._token_span(cte_start)))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            with_clause = ast.With(recursive, ctes, span=self._token_span(start))

        bodies: list[ast.Node] = [self._parse_query_body()]
        operators: list[tuple[str, bool]] = []
        precedence = {"union": 1, "except": 1, "intersect": 2}
        while self.at_keyword("UNION") or self.at_keyword("INTERSECT") or self.at_keyword("EXCEPT"):
            op = self.advance().text.casefold()
            all_rows = bool(self.accept_keyword("ALL"))
            if not all_rows:
                self.accept_keyword("DISTINCT")
            while operators and precedence[operators[-1][0]] >= precedence[op]:
                previous_op, previous_all = operators.pop()
                right = bodies.pop()
                left = bodies.pop()
                bodies.append(
                    ast.SetOperation(
                        previous_op, previous_all, self._as_query(left), self._as_query(right)
                    )
                )
            operators.append((op, all_rows))
            bodies.append(self._parse_query_body())
        while operators:
            op, all_rows = operators.pop()
            right = bodies.pop()
            left = bodies.pop()
            bodies.append(
                ast.SetOperation(op, all_rows, self._as_query(left), self._as_query(right))
            )

        order_by = self._parse_order_by() if self.accept_keyword("ORDER") else []
        distribute_by: list[ast.Expr] = []
        sort_by: list[ast.OrderItem] = []
        cluster_by: list[ast.Expr] = []
        while self.at_keyword("DISTRIBUTE", "SORT", "CLUSTER"):
            clause = self.advance().text.casefold()
            self.expect_keyword("BY")
            if clause == "distribute":
                distribute_by = self.parse_comma_list(self.parse_expr)
            elif clause == "sort":
                sort_by = self._parse_order_items()
            else:
                cluster_by = self.parse_comma_list(self.parse_expr)
        limit = self._parse_limit() if self._is_limit_start() else None
        return HiveQuery(
            with_clause,
            bodies[0],
            order_by,
            limit,
            distribute_by,
            sort_by,
            cluster_by,
            span=self._token_span(start),
        )

    def _parse_order_items(self) -> list[ast.OrderItem]:
        items = []
        while True:
            expr = self.parse_expr()
            desc = bool(self.accept_keyword("DESC"))
            if not desc:
                self.accept_keyword("ASC")
            items.append(ast.OrderItem(expr, desc))
            if not self.accept(TokenKind.PUNCT, ","):
                return items

    def _parse_query_body(self) -> ast.Node:
        if self.at_keyword("FROM"):
            return self._parse_from_first_select()
        return super()._parse_query_body()

    def _parse_from_first_select(self) -> ast.Select:
        start = self.expect_keyword("FROM")
        from_items = self._parse_from_list()
        select = self.parse_select()
        select.from_ = from_items
        select.span = self._range_span(start, self.tokens[self.index - 1])
        return select

    def parse_create(self) -> ast.Node:
        start_index = self.index
        start = self.expect_keyword("CREATE")
        replace = False
        if self.accept_keyword("OR"):
            self.expect_keyword("REPLACE")
            replace = True
        temporary = bool(self.accept_keyword("TEMPORARY") or self.accept_keyword("TEMP"))
        external = bool(self.accept_keyword("EXTERNAL"))
        if self.accept_keyword("TABLE"):
            return self._parse_hive_table(start, temporary, external)
        materialized = False
        if self.accept_keyword("MATERIALIZED"):
            materialized = True
            self.expect_keyword("VIEW")
        elif self.accept_keyword("VIEW"):
            pass
        else:
            if self.at_keyword("DATABASE", "SCHEMA"):
                kind = self.advance().text.casefold()
                if_not_exists = self._parse_if_not_exists()
                name = self.parse_qualified_name()
                options: dict[str, Any] = {}
                while not self._statement_end():
                    if self.accept_keyword("COMMENT"):
                        options["comment"] = self._parse_string_literal()
                    elif self.accept_keyword("LOCATION"):
                        options["location"] = self._parse_string_literal()
                    elif self.accept_keyword("WITH"):
                        self.expect_keyword("DBPROPERTIES")
                        options["dbproperties"] = self._parse_hive_properties()
                    else:
                        break
                if if_not_exists:
                    options["if_not_exists"] = True
                cls = ast.CreateSchema if kind == "schema" else ast.CreateDatabase
                return cls(name, options, span=self._token_span(start))
            if self.at_keyword("FUNCTION"):
                self.advance()
                if_not_exists = self._parse_if_not_exists()
                name = self.parse_qualified_name()
                self.expect_keyword("AS")
                class_name = self._parse_string_literal()
                resources = []
                if self.accept_keyword("USING"):
                    while True:
                        resource_type = self.advance().text.casefold()
                        if resource_type not in {"jar", "file", "archive"}:
                            raise ParseError(
                                "Expected JAR, FILE, or ARCHIVE.", self.tokens[self.index - 1]
                            )
                        uri = self._parse_string_literal()
                        resources.append({"type": resource_type, "uri": uri})
                        if not self.accept(TokenKind.PUNCT, ","):
                            break
                options: dict[str, Any] = {"class_name": class_name}
                if resources:
                    options["resources"] = resources
                if temporary:
                    options["temporary"] = True
                if if_not_exists:
                    options["if_not_exists"] = True
                return ast.CreateRoutine(
                    "function",
                    name,
                    language="java",
                    options=options,
                    span=self._token_span(start),
                )
            self.index = start_index
            return super().parse_create()

        if_not_exists = self._parse_if_not_exists()
        name = self.parse_qualified_name()
        columns: list[ast.Identifier] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")"):
                columns.append(self.parse_identifier())
                if self.accept_keyword("COMMENT"):
                    self._parse_string_literal()
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        options: dict[str, Any] = {}
        while not self._statement_end() and not self.at_keyword("AS"):
            if self.accept_keyword("COMMENT"):
                options["comment"] = self._parse_string_literal()
            elif self.accept_keyword("TBLPROPERTIES"):
                options["tblproperties"] = self._parse_hive_properties()
            else:
                break
        self.expect_keyword("AS")
        query = self.parse_query()
        if if_not_exists:
            options["if_not_exists"] = True
        return ast.CreateView(
            name,
            columns,
            query,
            materialized=materialized,
            replace=replace,
            options=options,
            span=self._token_span(start),
        )

    def _parse_hive_table(self, start: Token, temporary: bool, external: bool) -> ast.CreateTable:
        if_not_exists = self._parse_if_not_exists()
        name = self.parse_qualified_name()
        columns: list[ast.ColumnDef] = []
        constraints: list[ast.TableConstraint] = []
        like = None
        options: dict[str, Any] = {}
        if self.accept_keyword("LIKE"):
            like = self.parse_qualified_name()
        elif self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")"):
                if self._is_constraint_start():
                    constraints.append(self._parse_hive_table_constraint())
                else:
                    columns.append(self._parse_column_def())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
                if self.at(TokenKind.PUNCT, ")"):
                    raise ParseError("Expected a column definition after comma.", self.peek())
            self.expect(TokenKind.PUNCT, ")")

        while not self._statement_end() and not self.at_keyword("AS"):
            if self.accept_keyword("COMMENT"):
                options["comment"] = self._parse_string_literal()
            elif self.accept_keyword("PARTITIONED"):
                self.expect_keyword("BY")
                options["partitioned_by"] = self._parse_hive_column_list()
            elif self.accept_keyword("CLUSTERED"):
                options["clustered_by"] = self._parse_clustered_by()
            elif self.accept_keyword("SKEWED"):
                options["skewed_by"] = self._parse_skewed_by()
            elif self.accept_keyword("ROW"):
                self.expect_keyword("FORMAT")
                options["row_format"] = self._parse_row_format()
            elif self.accept_keyword("STORED"):
                if self.accept_keyword("AS"):
                    options.update(self._parse_stored_as())
                elif self.accept_keyword("BY"):
                    options["stored_by"] = self._parse_string_literal()
                    if self.accept_keyword("WITH"):
                        self.expect_keyword("SERDEPROPERTIES")
                        options.setdefault("row_format", {})["serde_properties"] = (
                            self._parse_hive_properties()
                        )
                else:
                    raise ParseError("Expected AS or BY after STORED.", self.peek())
            elif self.accept_keyword("LOCATION"):
                options["location"] = self._parse_string_literal()
            elif self.accept_keyword("TBLPROPERTIES"):
                options["tblproperties"] = self._parse_hive_properties()
            else:
                break

        if self.accept_keyword("AS"):
            as_query = self.parse_query()
        else:
            as_query = None
        return ast.CreateTable(
            name,
            columns,
            constraints,
            as_query,
            like,
            temporary=temporary,
            external=external,
            if_not_exists=if_not_exists,
            options=options,
            span=self._token_span(start),
        )

    def _parse_if_not_exists(self) -> bool:
        if not self.accept_keyword("IF"):
            return False
        self.expect_keyword("NOT")
        self.expect_keyword("EXISTS")
        return True

    def _parse_string_literal(self) -> ast.Literal:
        token = self.expect(TokenKind.STRING)
        return ast.Literal("string", token.value, span=self._token_span(token))

    def _parse_hive_column_list(self) -> list[ast.ColumnDef]:
        self.expect(TokenKind.PUNCT, "(")
        columns = self.parse_comma_list(self._parse_column_def)
        self.expect(TokenKind.PUNCT, ")")
        return columns

    def _parse_hive_table_constraint(self) -> ast.TableConstraint:
        constraint = self._parse_table_constraint()
        if self.accept_keyword("DISABLE"):
            constraint.options["disabled"] = True
        if self.accept_keyword("NOVALIDATE"):
            constraint.options["novalidate"] = True
        return constraint

    def _parse_clustered_by(self) -> dict[str, Any]:
        self.expect_keyword("BY")
        self.expect(TokenKind.PUNCT, "(")
        columns = self.parse_comma_list(self.parse_identifier)
        self.expect(TokenKind.PUNCT, ")")
        sorted_by: list[ast.OrderItem] = []
        if self.accept_keyword("SORTED"):
            self.expect_keyword("BY")
            self.expect(TokenKind.PUNCT, "(")
            sorted_by = self._parse_order_items()
            self.expect(TokenKind.PUNCT, ")")
        self.expect_keyword("INTO")
        buckets = self._parse_string_or_number_literal()
        self.expect_keyword("BUCKETS")
        return {"columns": columns, "sorted_by": sorted_by, "buckets": buckets}

    def _parse_skewed_by(self) -> dict[str, Any]:
        self.expect_keyword("BY")
        self.expect(TokenKind.PUNCT, "(")
        columns = self.parse_comma_list(self.parse_identifier)
        self.expect(TokenKind.PUNCT, ")")
        self.expect_keyword("ON")
        self.expect(TokenKind.PUNCT, "(")
        values = self.parse_comma_list(self.parse_expr)
        self.expect(TokenKind.PUNCT, ")")
        directories = False
        if self.accept_keyword("STORED"):
            self.expect_keyword("AS")
            self.expect_keyword("DIRECTORIES")
            directories = True
        return {"columns": columns, "values": values, "stored_as_directories": directories}

    def _parse_row_format(self) -> dict[str, Any]:
        if self.accept_keyword("DELIMITED"):
            result: dict[str, Any] = {"format": "delimited"}
            clauses = {
                "FIELDS": ("TERMINATED", "fields_terminated_by"),
                "COLLECTION": ("ITEMS", "collection_items_terminated_by"),
                "MAP": ("KEYS", "map_keys_terminated_by"),
                "LINES": ("TERMINATED", "lines_terminated_by"),
                "NULL": ("DEFINED", "null_defined_as"),
            }
            while any(self.at_keyword(word) for word in clauses):
                key = self.advance().text.upper()
                middle, option = clauses[key]
                self.expect_keyword(middle)
                if key == "NULL":
                    self.expect_keyword("AS")
                else:
                    self.expect_keyword("BY")
                result[option] = self._parse_string_literal()
                if key == "FIELDS" and self.accept_keyword("ESCAPED"):
                    self.expect_keyword("BY")
                    result["escaped_by"] = self._parse_string_literal()
            return result
        if self.accept_keyword("SERDE"):
            result = {"format": "serde", "serde": self._parse_string_literal()}
            if self.accept_keyword("WITH"):
                self.expect_keyword("SERDEPROPERTIES")
                result["serde_properties"] = self._parse_hive_properties()
            return result
        raise ParseError("Expected DELIMITED or SERDE.", self.peek())

    def _parse_stored_as(self) -> dict[str, Any]:
        if self.accept_keyword("INPUTFORMAT"):
            input_format = self._parse_string_literal()
            self.expect_keyword("OUTPUTFORMAT")
            return {
                "stored_as_formats": {
                    "input_format": input_format,
                    "output_format": self._parse_string_literal(),
                }
            }
        token = self.advance()
        if token.kind not in {TokenKind.IDENT, TokenKind.QUOTED_IDENT}:
            raise ParseError("Expected storage format.", token)
        return {"stored_as": token.value.upper()}

    def _parse_hive_properties(self) -> dict[str, ast.Literal]:
        self.expect(TokenKind.PUNCT, "(")
        properties = {}
        while not self.at(TokenKind.PUNCT, ")"):
            key = self.advance()
            if key.kind not in {TokenKind.STRING, TokenKind.IDENT, TokenKind.QUOTED_IDENT}:
                raise ParseError("Expected property name.", key)
            self.expect(TokenKind.OPERATOR, "=")
            properties[key.value] = self._parse_string_literal()
            if not self.accept(TokenKind.PUNCT, ","):
                break
        self.expect(TokenKind.PUNCT, ")")
        return properties

    def _parse_string_or_number_literal(self) -> ast.Literal:
        token = self.peek()
        if token.kind is TokenKind.STRING:
            return self._parse_string_literal()
        if token.kind is TokenKind.NUMBER:
            self.advance()
            return ast.Literal("number", token.value, span=self._token_span(token))
        raise ParseError("Expected string or numeric literal.", token)

    def parse_insert(self) -> ast.Node:
        start = self.expect_keyword("INSERT")
        overwrite = bool(self.accept_keyword("OVERWRITE"))
        local = bool(self.accept_keyword("LOCAL")) if overwrite else False
        if self.accept_keyword("DIRECTORY"):
            path = self._parse_string_literal()
            row_format = None
            if self.accept_keyword("ROW"):
                self.expect_keyword("FORMAT")
                row_format = self._parse_row_format()
            stored_as = None
            if self.accept_keyword("STORED"):
                self.expect_keyword("AS")
                stored_as = self._parse_stored_as()
            return ast.DialectStatement(
                "insert_directory",
                {
                    "local": local,
                    "path": path,
                    "row_format": row_format,
                    "stored_as": stored_as,
                    "query": self.parse_query(),
                },
                span=self._token_span(start),
            )
        if not overwrite:
            self.expect_keyword("INTO")
        self.accept_keyword("TABLE")
        target = self.parse_qualified_name()
        partition = self._parse_partition_spec() if self.accept_keyword("PARTITION") else []
        if self.accept_keyword("IF"):
            self.expect_keyword("NOT")
            self.expect_keyword("EXISTS")
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        source = self.parse_values() if self.at_keyword("VALUES") else self.parse_query()
        return ast.Insert(
            target,
            columns,
            source,
            overwrite,
            partition,
            span=self._token_span(start),
        )

    def _parse_partition_spec(self) -> list[ast.Assignment]:
        self.expect(TokenKind.PUNCT, "(")
        partitions = []
        if not self.at(TokenKind.PUNCT, ")"):
            while True:
                name = self.parse_identifier()
                target = ast.ColumnRef(ast.QualifiedName(parts=[name], span=name.span))
                value = self.parse_expr(31) if self.accept(TokenKind.OPERATOR, "=") else None
                partitions.append(ast.Assignment(target, value))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
        self.expect(TokenKind.PUNCT, ")")
        return partitions

    def parse_statement_ext(self) -> ast.Node | None:
        if self.at_keyword("USE"):
            start = self.advance()
            database = self.parse_qualified_name()
            self.default_schema = ".".join(part.value for part in database.parts)
            return ast.Use(database, span=self._range_span(start, self.tokens[self.index - 1]))
        if self.at_keyword("FROM"):
            if self._has_top_level_keyword("INSERT"):
                return self._parse_multi_insert()
            return self.parse_query()
        if self.at_keyword("SET"):
            return self._parse_hive_set()
        if self.at_keyword("RESET"):
            self.advance()
            key = self._consume_to_end().strip()
            return ast.DialectStatement("reset", {"key": key})
        if self.at_keyword("ADD"):
            return self._parse_add_resource()
        if self.at_keyword("LIST"):
            return self._parse_list_resources()
        if self.at_keyword("DELETE") and self.peek(1).text.upper() in {"JAR", "FILE", "ARCHIVE"}:
            return self._parse_delete_resource()
        if self.at_keyword("LOAD"):
            return self._parse_load_data()
        if self.at_keyword("MSCK"):
            return self._parse_msck()
        if self.at_keyword("ANALYZE"):
            return self._parse_analyze()
        if self.at_keyword("TRUNCATE"):
            return self._parse_hive_truncate()
        if self.at_keyword("SHOW"):
            return self._parse_show()
        if self.at_keyword("DESCRIBE", "DESC"):
            return self._parse_describe()
        if self.at_keyword("DROP") and self.peek(1).text.upper() in {
            "FUNCTION",
            "TEMPORARY",
        }:
            return self._parse_drop_function()
        return None

    def _has_top_level_keyword(self, keyword: str) -> bool:
        depth = 0
        for token in self.tokens[self.index + 1 :]:
            if token.kind is TokenKind.TERMINATOR or token.kind is TokenKind.EOF:
                return False
            if token.kind is TokenKind.PUNCT:
                if token.text in {"(", "["}:
                    depth += 1
                elif token.text in {")", "]"}:
                    depth = max(0, depth - 1)
            elif depth == 0 and token.kind is TokenKind.IDENT and token.text.upper() == keyword:
                return True
        return False

    def _parse_multi_insert(self) -> ast.DialectStatement:
        start = self.expect_keyword("FROM")
        from_items = self._parse_from_list()
        inserts = []
        while self.accept_keyword("INSERT"):
            inserts.append(self._parse_multi_insert_clause())
        if not inserts:
            raise ParseError("Expected INSERT after FROM source.", self.peek())
        return ast.DialectStatement(
            "multi_insert",
            {"from": from_items, "inserts": inserts},
            span=self._token_span(start),
        )

    def _parse_multi_insert_clause(self) -> ast.Insert:
        overwrite = bool(self.accept_keyword("OVERWRITE"))
        if not overwrite:
            self.expect_keyword("INTO")
        self.accept_keyword("TABLE")
        target = self.parse_qualified_name()
        partition = self._parse_partition_spec() if self.accept_keyword("PARTITION") else []
        if self.accept_keyword("IF"):
            self.expect_keyword("NOT")
            self.expect_keyword("EXISTS")
        columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
        source = self.parse_query()
        if not isinstance(source.body, ast.Select):
            raise ParseError("Expected SELECT in multi-insert.", self.peek())
        source.body.from_ = []
        return ast.Insert(target, columns, source, overwrite, partition)

    def _parse_from_item(self) -> ast.Node:
        if self.at_keyword("LATERAL") and self.peek(1).text.upper() == "VIEW":
            raise ParseError("LATERAL VIEW requires a preceding FROM item.", self.peek())
        lateral = bool(self.accept_keyword("LATERAL"))
        start = self.peek()
        if self.accept(TokenKind.PUNCT, "("):
            if self.at_keyword("SELECT", "WITH", "VALUES", "FROM"):
                query = self.parse_query()
                self.expect(TokenKind.PUNCT, ")")
                item: ast.Node = ast.SubqueryRef(query, self._parse_optional_alias(), lateral)
            else:
                item = self._parse_join()
                self.expect(TokenKind.PUNCT, ")")
        elif self.at_keyword("VALUES"):
            values = self.parse_values()
            alias = self._parse_optional_alias()
            columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
            item = ast.ValuesRef(values, alias, columns, span=self._token_span(start))
        else:
            name = self.parse_qualified_name()
            if self.at(TokenKind.PUNCT, "("):
                call = self._parse_postfix(ast.ColumnRef(name))
                item = ast.TableFunctionRef(call, self._parse_optional_alias(), lateral)
            else:
                alias = self._parse_optional_alias()
                columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
                sample = None
                if self.accept_keyword("TABLESAMPLE"):
                    self.expect(TokenKind.PUNCT, "(")
                    if self.accept_keyword("BUCKET"):
                        bucket = self.parse_expr(80)
                        self.expect_keyword("OUT")
                        self.expect_keyword("OF")
                        out_of = self.parse_expr(80)
                        on = self.parse_expr(80) if self.accept_keyword("ON") else None
                        self.expect(TokenKind.PUNCT, ")")
                        sample = ast.DialectExpr(
                            "bucket_sample",
                            {"bucket": bucket, "out_of": out_of, "on": on},
                        )
                    else:
                        sample = self.parse_expr()
                        self.expect(TokenKind.PUNCT, ")")
                item = ast.TableRef(
                    name, alias, columns, sample=sample, span=self._token_span(start)
                )
        while self.at_keyword("LATERAL") and self.peek(1).text.upper() == "VIEW":
            item = ast.Join("cross", item, self._parse_lateral_view())
        return item

    def _parse_lateral_view(self) -> ast.DialectExpr:
        self.expect_keyword("LATERAL")
        self.expect_keyword("VIEW")
        outer = bool(self.accept_keyword("OUTER"))
        call = self.parse_expr()
        if not isinstance(call, ast.FunctionCall):
            raise ParseError("Expected table-generating function.", self.peek())
        table_alias = self.parse_identifier()
        self.expect_keyword("AS")
        column_aliases = self.parse_comma_list(self.parse_identifier)
        return ast.DialectExpr(
            "lateral_view",
            {
                "call": call,
                "table_alias": table_alias,
                "column_aliases": column_aliases,
                "outer": "true" if outer else None,
            },
        )

    def _parse_hive_set(self) -> ast.SetStatement:
        start = self.expect_keyword("SET")
        if self._statement_end():
            return ast.SetStatement(items=[], span=self._token_span(start))
        if self.at(TokenKind.OPERATOR, "-") and self.peek(1).text.casefold() == "v":
            self.advance()
            self.advance()
            return ast.SetStatement(scope="-v", items=[], span=self._token_span(start))
        if not any(token.text == "=" for token in self.tokens[self.index :]):
            scope = self._consume_to_end().strip() or None
            return ast.SetStatement(scope=scope, items=[], span=self._token_span(start))

        items: list[ast.SetItem] = []
        scope = None
        while not self._statement_end():
            key_start = self.peek().offset
            while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR):
                if self.at(TokenKind.OPERATOR, "="):
                    break
                self.advance()
            if not self.at(TokenKind.OPERATOR, "="):
                raise ParseError("Expected SET assignment operator.", self.peek())
            equals = self.advance()
            key_raw = self.text[key_start : equals.offset].strip()
            value_start = self.peek().offset
            while not self._statement_end() and not self.at(TokenKind.PUNCT, ","):
                self.advance()
            value_end = self.peek().offset
            value_raw = self.text[value_start:value_end].strip()
            match = re.match(r"^(?:\$\{)?(hivevar|hiveconf|system|env):(.+?)}?$", key_raw, re.I)
            if match:
                scope = scope or match.group(1).casefold()
                key_raw = match.group(2)
            parts = [ast.Identifier(value=part.strip('`"')) for part in key_raw.split(".") if part]
            name = ast.ColumnRef(ast.QualifiedName(parts=parts))
            items.append(
                ast.SetItem(
                    name,
                    ast.Literal("string", value_raw),
                )
            )
            if not self.accept(TokenKind.PUNCT, ","):
                break
        return ast.SetStatement(scope, items, span=self._token_span(start))

    def _parse_add_resource(self) -> ast.DialectStatement:
        start = self.expect_keyword("ADD")
        resource_type = self.advance().text.casefold()
        if resource_type not in {"jar", "file", "archive"}:
            raise ParseError("Expected JAR, FILE, or ARCHIVE.", self.tokens[self.index - 1])
        raw = self._consume_to_end().strip()
        paths = [ast.Literal("string", raw)] if raw else []
        return ast.DialectStatement(
            "add_resource",
            {"resource_type": resource_type, "paths": paths},
            span=self._token_span(start),
        )

    def _parse_list_resources(self) -> ast.DialectStatement:
        start = self.expect_keyword("LIST")
        resource_type = self.advance().text.casefold()
        return ast.DialectStatement(
            "list_resources", {"resource_type": resource_type}, span=self._token_span(start)
        )

    def _parse_delete_resource(self) -> ast.DialectStatement:
        start = self.expect_keyword("DELETE")
        resource_type = self.advance().text.casefold()
        raw = self._consume_to_end().strip()
        return ast.DialectStatement(
            "delete_resource",
            {"resource_type": resource_type, "path": ast.Literal("string", raw)},
            span=self._token_span(start),
        )

    def _parse_load_data(self) -> ast.DialectStatement:
        start = self.expect_keyword("LOAD")
        self.expect_keyword("DATA")
        local = bool(self.accept_keyword("LOCAL"))
        self.expect_keyword("INPATH")
        path = self._parse_string_literal()
        overwrite = bool(self.accept_keyword("OVERWRITE"))
        self.expect_keyword("INTO")
        self.accept_keyword("TABLE")
        table = self.parse_qualified_name()
        partition = self._parse_partition_spec() if self.accept_keyword("PARTITION") else []
        return ast.DialectStatement(
            "load_data",
            {
                "local": local,
                "path": path,
                "overwrite": overwrite,
                "table": table,
                "partition": partition,
            },
            [ast.RefHint(table, "writes", span=self._token_span(start))],
            span=self._token_span(start),
        )

    def _parse_msck(self) -> ast.DialectStatement:
        start = self.expect_keyword("MSCK")
        self.accept_keyword("REPAIR")
        self.expect_keyword("TABLE")
        table = self.parse_qualified_name()
        action = self.advance().text.casefold() if self.at_keyword("ADD", "DROP", "SYNC") else None
        if action:
            self.accept_keyword("PARTITIONS")
        return ast.DialectStatement(
            "msck_repair",
            {"table": table, "action": action},
            [ast.RefHint(table, "writes", span=self._token_span(start))],
            span=self._token_span(start),
        )

    def _parse_analyze(self) -> ast.DialectStatement:
        start = self.expect_keyword("ANALYZE")
        self.expect_keyword("TABLE")
        table = self.parse_qualified_name()
        partition = self._parse_partition_spec() if self.accept_keyword("PARTITION") else []
        self.expect_keyword("COMPUTE")
        self.expect_keyword("STATISTICS")
        for_columns = False
        if self.accept_keyword("FOR"):
            self.expect_keyword("COLUMNS")
            for_columns = True
        return ast.DialectStatement(
            "analyze",
            {"table": table, "partition": partition, "for_columns": for_columns},
            [ast.RefHint(table, "reads", span=self._token_span(start))],
            span=self._token_span(start),
        )

    def _parse_hive_truncate(self) -> ast.Node:
        start = self.expect_keyword("TRUNCATE")
        self.accept_keyword("TABLE")
        table = self.parse_qualified_name()
        partition = self._parse_partition_spec() if self.accept_keyword("PARTITION") else []
        if partition:
            return ast.DialectStatement(
                "truncate_table",
                {"table": table, "partition": partition},
                [ast.RefHint(table, "writes", span=self._token_span(start))],
                span=self._token_span(start),
            )
        return ast.Truncate(table, span=self._token_span(start))

    def _parse_show(self) -> ast.DialectStatement:
        start = self.expect_keyword("SHOW")
        show_kind = self.advance().text.casefold()
        args: dict[str, Any] = {"kind": show_kind}
        if show_kind in {"tables", "functions", "views", "partitions"} and self.accept_keyword(
            "IN", "FROM"
        ):
            args["database"] = self.parse_qualified_name()
        if self.accept_keyword("LIKE"):
            args["pattern"] = self._parse_string_literal()
        return ast.DialectStatement("show", args, span=self._token_span(start))

    def _parse_describe(self) -> ast.DialectStatement:
        start = self.advance()
        mode = None
        if self.at_keyword("FORMATTED", "EXTENDED"):
            mode = self.advance().text.casefold()
        object_kind = (
            self.advance().text.casefold()
            if self.at_keyword("DATABASE", "SCHEMA", "TABLE", "VIEW", "FUNCTION")
            else "table"
        )
        target = self.parse_qualified_name()
        partition = self._parse_partition_spec() if self.accept_keyword("PARTITION") else []
        column = self.parse_identifier() if self._is_name() else None
        return ast.DialectStatement(
            "describe",
            {
                "mode": mode,
                "object_kind": object_kind,
                "target": target,
                "partition": partition,
                "column": column,
            },
            span=self._token_span(start),
        )

    def _parse_drop_function(self) -> ast.Drop:
        start = self.expect_keyword("DROP")
        temporary = bool(self.accept_keyword("TEMPORARY"))
        self.expect_keyword("FUNCTION")
        if_exists = False
        if self.accept_keyword("IF"):
            self.expect_keyword("EXISTS")
            if_exists = True
        name = self.parse_qualified_name()
        return ast.Drop(
            "temporary function" if temporary else "function",
            [name],
            if_exists,
            span=self._token_span(start),
        )

    def parse_alter(self) -> ast.Alter:
        start = self.expect_keyword("ALTER")
        object_kind = self.advance().text.casefold()
        name = self.parse_qualified_name()
        actions: list[ast.Node] = []
        if object_kind == "view" and self.accept_keyword("AS"):
            query = self.parse_query()
            actions.append(ast.DialectStatement("set_query", {"query": query}))
        elif object_kind == "table":
            if self.accept_keyword("ADD"):
                if self.accept_keyword("IF"):
                    self.expect_keyword("NOT")
                    self.expect_keyword("EXISTS")
                    if_not_exists = True
                else:
                    if_not_exists = False
                if self.accept_keyword("PARTITION"):
                    partitions = []
                    while True:
                        spec = self._parse_partition_spec()
                        location = (
                            self._parse_string_literal()
                            if self.accept_keyword("LOCATION")
                            else None
                        )
                        partitions.append({"spec": spec, "location": location})
                        if self.accept(TokenKind.PUNCT, ","):
                            self.expect_keyword("PARTITION")
                        elif not self.accept_keyword("PARTITION"):
                            break
                    actions.append(
                        ast.DialectStatement(
                            "add_partition",
                            {"if_not_exists": if_not_exists, "partitions": partitions},
                        )
                    )
                elif self.accept_keyword("COLUMNS"):
                    actions.append(
                        ast.DialectStatement(
                            "add_columns", {"columns": self._parse_hive_column_list()}
                        )
                    )
                else:
                    actions.append(self._parse_hive_table_constraint())
            elif self.accept_keyword("REPLACE"):
                self.expect_keyword("COLUMNS")
                actions.append(
                    ast.DialectStatement(
                        "replace_columns", {"columns": self._parse_hive_column_list()}
                    )
                )
            elif self.accept_keyword("DROP"):
                if_exists = False
                if self.accept_keyword("IF"):
                    self.expect_keyword("EXISTS")
                    if_exists = True
                self.expect_keyword("PARTITION")
                partitions = [self._parse_partition_spec()]
                while self.accept(TokenKind.PUNCT, ","):
                    self.expect_keyword("PARTITION")
                    partitions.append(self._parse_partition_spec())
                purge = bool(self.accept_keyword("PURGE"))
                actions.append(
                    ast.DialectStatement(
                        "drop_partition",
                        {"if_exists": if_exists, "partitions": partitions, "purge": purge},
                    )
                )
            elif self.accept_keyword("PARTITION"):
                source = self._parse_partition_spec()
                self.expect_keyword("RENAME")
                self.expect_keyword("TO")
                self.expect_keyword("PARTITION")
                actions.append(
                    ast.DialectStatement(
                        "rename_partition",
                        {"partition": source, "to": self._parse_partition_spec()},
                    )
                )
            elif self.accept_keyword("CHANGE"):
                self.accept_keyword("COLUMN")
                old_name = self.parse_identifier()
                new_name = self.parse_identifier()
                data_type = self.parse_data_type()
                comment = self._parse_string_literal() if self.accept_keyword("COMMENT") else None
                position = None
                if self.accept_keyword("FIRST"):
                    position = "first"
                elif self.accept_keyword("AFTER"):
                    position = self.parse_identifier()
                actions.append(
                    ast.DialectStatement(
                        "change_column",
                        {
                            "old_name": old_name,
                            "new_name": new_name,
                            "type": data_type,
                            "comment": comment,
                            "position": position,
                        },
                    )
                )
            elif self.accept_keyword("SET"):
                if self.accept_keyword("TBLPROPERTIES"):
                    actions.append(
                        ast.DialectStatement(
                            "set_tblproperties", {"properties": self._parse_hive_properties()}
                        )
                    )
                elif self.accept_keyword("LOCATION"):
                    actions.append(
                        ast.DialectStatement(
                            "set_location", {"location": self._parse_string_literal()}
                        )
                    )
                elif self.accept_keyword("SERDEPROPERTIES"):
                    actions.append(
                        ast.DialectStatement(
                            "set_serdeproperties", {"properties": self._parse_hive_properties()}
                        )
                    )
                else:
                    raise ParseError("Unsupported ALTER TABLE SET action.", self.peek())
            elif self.accept_keyword("RENAME"):
                self.expect_keyword("TO")
                actions.append(ast.Rename("table", name, self.parse_qualified_name()))
            else:
                raise ParseError("Unsupported ALTER TABLE action.", self.peek())
        else:
            actions.append(
                ast.DialectStatement("action", {"tokens": self._collect_to_statement_end()})
            )
        return ast.Alter(object_kind, name, actions, span=self._token_span(start))

    def _collect_to_statement_end(self) -> list[str]:
        values = []
        while not self._statement_end():
            values.append(self.advance().text)
        return values

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
            "FROM",
            "SET",
            "USE",
            "ADD",
            "MSCK",
            "LOAD",
            "ANALYZE",
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
