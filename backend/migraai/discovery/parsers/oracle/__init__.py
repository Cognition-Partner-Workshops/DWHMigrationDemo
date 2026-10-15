from __future__ import annotations

import re
from typing import Any

from migraai.discovery import ast
from migraai.discovery.lexer import Lexer, Token, TokenKind
from migraai.discovery.parser_base import ParseError, ParserBase

from .lexer import LEXER_CONFIG
from .nodes import OracleFetch, OracleSelect

_SQLPLUS_OPTIONS = frozenset(
    {
        "SERVEROUTPUT",
        "ECHO",
        "FEEDBACK",
        "HEADING",
        "LINESIZE",
        "PAGESIZE",
        "TIMING",
        "VERIFY",
        "TERMOUT",
        "DEFINE",
        "TRIMSPOOL",
        "SQLBLANKLINES",
        "LONG",
        "ESCAPE",
        "CONCAT",
        "NEWPAGE",
        "WRAP",
        "ARRAYSIZE",
    }
)
_SQLPLUS_COMMANDS = frozenset(
    {
        "SPOOL",
        "PROMPT",
        "WHENEVER",
        "CONNECT",
        "CONN",
        "DEFINE",
        "UNDEFINE",
        "EXIT",
        "QUIT",
        "SHOW",
        "COLUMN",
        "TTITLE",
        "BTITLE",
        "BREAK",
        "COMPUTE",
        "REM",
        "REMARK",
        "VARIABLE",
        "VAR",
        "ACCEPT",
        "PAUSE",
        "CLEAR",
        "STORE",
        "START",
    }
)
_PSEUDO_COLUMNS = frozenset(
    {"ROWNUM", "ROWID", "LEVEL", "SYSDATE", "SYSTIMESTAMP", "CONNECT_BY_ISLEAF"}
)
_ORACLE_BUILTINS = frozenset(
    {
        "ADD_MONTHS",
        "CARDINALITY",
        "COALESCE",
        "DECODE",
        "EMPTY_BLOB",
        "EMPTY_CLOB",
        "FIRST",
        "LAST",
        "LISTAGG",
        "LNNVL",
        "MONTHS_BETWEEN",
        "NANVL",
        "NCHR",
        "NLS_INITCAP",
        "NLS_LOWER",
        "NLS_UPPER",
        "NLSSORT",
        "NVL",
        "NVL2",
        "ORA_HASH",
        "POWER",
        "REGEXP_COUNT",
        "REGEXP_INSTR",
        "REGEXP_REPLACE",
        "REGEXP_SUBSTR",
        "REMAINDER",
        "ROUND",
        "SIGN",
        "SQLCODE",
        "SQLERRM",
        "SYS_CONTEXT",
        "SYS_GUID",
        "SYSDATE",
        "SYSTIMESTAMP",
        "TO_CHAR",
        "TO_CLOB",
        "TO_DATE",
        "TO_NUMBER",
        "TO_TIMESTAMP",
        "TRUNC",
        "UID",
        "USERENV",
        "VALIDATE_CONVERSION",
        "WIDTH_BUCKET",
        "RAISE_APPLICATION_ERROR",
    }
)
_ORACLE_BINARY_OPS = {
    **ParserBase.binary_ops,
    "||": (50, 51, "||"),
    "**": (70, 70, "**"),
}


class Parser(ParserBase):
    builtin_functions = ParserBase.ansi_builtins | _ORACLE_BUILTINS
    binary_ops = _ORACLE_BINARY_OPS

    def __init__(self, text: str | None = None, rel_path: str = "") -> None:
        super().__init__(text, rel_path, lexer_config=LEXER_CONFIG)

    def parse(self, text: str | None = None, rel_path: str | None = None) -> ast.ParseResult:
        if text is not None:
            self.text = text
        if rel_path is not None:
            self.rel_path = rel_path
        lexer_issues: list[ast.Issue] = []
        if text is not None or not self.tokens:
            lexer = Lexer(self.text, self.lexer_config)
            self.tokens = self._sqlplus_tokens(lexer.tokenize())
            lexer_issues = list(lexer.issues)
        result = super().parse(None, None)
        if lexer_issues:
            result.issues[0:0] = lexer_issues
        return result

    def _sqlplus_tokens(self, tokens: list[Token]) -> list[Token]:
        result: list[Token] = []
        statement_start = True
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.kind is TokenKind.EOF:
                result.append(token)
                break
            if token.kind is TokenKind.TERMINATOR:
                result.append(token)
                statement_start = True
                index += 1
                continue
            if token.kind is TokenKind.DOT_COMMAND:
                result.append(token)
                statement_start = True
                index += 1
                continue
            line_start = self.text.rfind("\n", 0, token.offset) + 1
            line_end = self.text.find("\n", token.offset)
            if line_end < 0:
                line_end = len(self.text)
            raw_line = self.text[line_start:line_end].rstrip("\r")
            prefix = self.text[line_start : token.offset]
            command = (
                self._sqlplus_command(raw_line) if statement_start and not prefix.strip() else None
            )
            if command is not None:
                result.append(
                    Token(
                        TokenKind.DOT_COMMAND, command, command, token.line, token.col, token.offset
                    )
                )
                while index < len(tokens) and tokens[index].line == token.line:
                    index += 1
                statement_start = True
                continue
            result.append(token)
            statement_start = False
            index += 1
        return result

    def _sqlplus_command(self, line: str) -> str | None:
        stripped = line.strip()
        if not stripped:
            return None
        if re.match(r"^@@?\s*\S+", stripped):
            return stripped
        match = re.match(r"^([A-Za-z]+)\b(.*)$", stripped)
        if match is None:
            return None
        command, rest = match.group(1), match.group(2)
        upper = command.upper()
        if upper == "SET":
            option = rest.strip().split(None, 1)[0].upper() if rest.strip() else ""
            return stripped if option in _SQLPLUS_OPTIONS else None
        if upper not in _SQLPLUS_COMMANDS:
            return None
        if upper in {"CONNECT", "CONN"}:
            credential = re.match(r"^(\S+)(.*)$", rest.lstrip())
            if credential is not None:
                account = credential.group(1)
                suffix = credential.group(2)
                account = re.sub(r"^([^/]+)/([^@\s]+)", r"\1/***", account, count=1)
                gap = " " if rest[:1].isspace() else ""
                return f"{command}{gap}{account}{suffix}".rstrip()
        if upper == "DEFINE":
            definition = re.match(r"^(\s*)([A-Za-z_][A-Za-z_0-9]*)(\s*=\s*).*$", rest)
            if definition and re.search(
                r"(?:PASSWORD|PASSWD|SECRET|TOKEN|CREDENTIAL)", definition.group(2), re.I
            ):
                return (
                    f"{command}{definition.group(1)}{definition.group(2)}{definition.group(3)}***"
                )
        return stripped

    def parse_qualified_name(self) -> ast.QualifiedName:
        name = super().parse_qualified_name()
        if self.accept(TokenKind.OPERATOR, "@"):
            link_parts = [self.parse_identifier().value]
            while self.accept(TokenKind.PUNCT, "."):
                link_parts.append(self.parse_identifier().value)
            if name.parts:
                name.parts[-1].value = f"{name.parts[-1].value}@{'.'.join(link_parts)}"
        return name

    def parse_data_type(self) -> ast.DataType:
        start = self.peek()
        name = self.parse_qualified_name()
        if self.accept(TokenKind.OPERATOR, "%"):
            attribute = self.parse_identifier().value.upper()
            if attribute not in {"TYPE", "ROWTYPE"}:
                raise ParseError("Expected TYPE or ROWTYPE after %.", self.peek())
            anchor = ast.ColumnRef(name, span=self._token_span(start))
            return ast.DataType(attribute, [anchor], span=self._token_span(start))
        params: list[ast.Node | str] = []
        type_name = name.parts[-1].value.casefold() if name.parts else ""
        if self.accept(TokenKind.PUNCT, "("):
            if not self.at(TokenKind.PUNCT, ")"):
                params.append(self.parse_expr(80))
                if (
                    type_name in {"varchar2", "nvarchar2", "char", "nchar", "varchar"}
                    and self._is_name()
                ):
                    params.append(self.advance().text.casefold())
                while self.accept(TokenKind.PUNCT, ","):
                    params.append(self.parse_expr(80))
            self.expect(TokenKind.PUNCT, ")")
        if type_name in {"timestamp", "time"} and self.accept_keyword("WITH", "WITHOUT"):
            modifier = self.tokens[self.index - 1].text.casefold()
            name.parts.append(ast.Identifier(value=modifier))
            if self.accept_keyword("TIME"):
                name.parts.append(ast.Identifier(value="time"))
            if self.accept_keyword("ZONE"):
                name.parts.append(ast.Identifier(value="zone"))
        return ast.DataType(
            name,
            params,
            span=self._range_span(start, self.tokens[self.index - 1]),
        )

    def _can_be_alias(self) -> bool:
        return super()._can_be_alias() and not self.at_keyword(
            "START",
            "CONNECT",
            "GROUP",
            "HAVING",
            "ORDER",
            "OFFSET",
            "FETCH",
            "FOR",
            "PIVOT",
            "UNPIVOT",
            "MODEL",
        )

    def parse_primary_ext(self) -> ast.Node | None:
        if self.at(TokenKind.OPERATOR, ":") and self.peek(1).kind is TokenKind.NUMBER:
            start = self.advance()
            end = self.advance()
            return ast.Param(f":{end.text}", span=self._range_span(start, end))
        if self.at_keyword("PRIOR", "CONNECT_BY_ROOT"):
            token = self.advance()
            value = self.parse_expr(80)
            name = "prior" if token.text.upper() == "PRIOR" else "connect_by_root"
            return ast.DialectExpr(name=name, args={"expr": value}, span=self._token_span(token))
        if self.at_keyword(*_PSEUDO_COLUMNS) and self.peek(1).text not in {".", "("}:
            token = self.advance()
            return ast.DialectExpr(
                name="pseudo_column",
                args={"name": token.text.upper()},
                span=self._token_span(token),
            )
        return None

    def parse_postfix_ext(self, left: ast.Node) -> ast.Node | None:
        if self.accept(TokenKind.OPERATOR, "(+)"):
            return ast.DialectExpr(name="outer_join_marker", args={"expr": left})
        if self.accept(TokenKind.OPERATOR, "%") and isinstance(left, ast.ColumnRef):
            attribute = self.parse_identifier()
            return ast.DialectExpr(
                name="cursor_attribute",
                args={"cursor": left, "attribute": attribute.value.upper()},
                span=left.span,
            )
        if isinstance(left, ast.ColumnRef) and left.name.parts:
            last = left.name.parts[-1].value.upper()
            if last in {"NEXTVAL", "CURRVAL"} and len(left.name.parts) > 1:
                return ast.DialectExpr(
                    name="sequence_value",
                    args={
                        "sequence": ast.QualifiedName(parts=left.name.parts[:-1]),
                        "op": last.casefold(),
                    },
                    span=left.span,
                )
        return None

    def parse_statement_ext(self) -> ast.Node | None:
        if self.at(TokenKind.OPERATOR, "<") and self.peek(1).text == "<":
            return self._parse_labeled_statement()
        if self.at_keyword("DECLARE"):
            return self._parse_declared_block()
        if self.at_keyword("EXECUTE") and self.at_keyword("IMMEDIATE", 1):
            return self._parse_execute_immediate()
        if self.at_keyword("FORALL"):
            return self._parse_forall()
        if self.at_keyword("IF"):
            return self.parse_if()
        if self.at_keyword("WHILE"):
            return self.parse_while()
        if self.at_keyword("LOOP"):
            return self.parse_loop()
        if self.at_keyword("EXIT", "CONTINUE"):
            return self._parse_conditional_loop_control()
        if self.at_keyword("NULL"):
            self.advance()
            return ast.DialectStatement(name="null_statement")
        if self.at_keyword("OPEN"):
            return self._parse_oracle_open()
        if self.at_keyword("FETCH"):
            return self._parse_oracle_fetch()
        if self.at_keyword("CLOSE"):
            self.advance()
            return ast.CloseCursor(name=self.parse_identifier())
        if self.at_keyword("SAVEPOINT"):
            self.advance()
            return ast.DialectStatement(name="savepoint", args={"name": self.parse_identifier()})
        if self.at_keyword("CASE"):
            return self._parse_case_statement()
        if self._is_oracle_call_statement():
            return self._parse_oracle_call()
        return None

    def _parse_labeled_statement(self) -> ast.Node:
        self.expect(TokenKind.OPERATOR, "<")
        self.expect(TokenKind.OPERATOR, "<")
        label = self.parse_identifier().value
        self.expect(TokenKind.OPERATOR, ">")
        self.expect(TokenKind.OPERATOR, ">")
        statement = self.parse_statement()
        if isinstance(statement, (ast.Block, ast.Loop)):
            statement.label = label
            return statement
        return ast.DialectStatement(
            name="labeled_statement",
            args={"label": label, "statement": statement},
        )

    def _is_oracle_call_statement(self) -> bool:
        if not self._is_name():
            return False
        offset = 0
        while True:
            if self.peek(offset).kind not in {TokenKind.IDENT, TokenKind.QUOTED_IDENT}:
                return False
            offset += 1
            if self.peek(offset).text != ".":
                break
            offset += 1
        return self.peek(offset).text in {"(", ";", "/"} or self.peek(offset).kind in {
            TokenKind.TERMINATOR,
            TokenKind.EOF,
        }

    def _parse_oracle_call(self) -> ast.Call:
        name = self.parse_qualified_name()
        args: list[ast.Node] = []
        if self.accept(TokenKind.PUNCT, "("):
            args = (
                self.parse_comma_list(self._parse_function_arg)
                if not self.at(TokenKind.PUNCT, ")")
                else []
            )
            self.expect(TokenKind.PUNCT, ")")
        return ast.Call(name=name, args=args)

    def _parse_declared_block(self) -> ast.Block:
        self.expect_keyword("DECLARE")
        declarations = self._parse_oracle_declarations({"BEGIN"})
        block = self.parse_block()
        block.declarations = declarations + block.declarations
        return block

    def _parse_oracle_declarations(self, stop: set[str]) -> list[ast.Node]:
        declarations: list[ast.Node] = []
        while not self.at(TokenKind.EOF) and not any(self.at_keyword(word) for word in stop):
            if self.at(TokenKind.TERMINATOR):
                self.advance()
                continue
            if self.at_keyword("PROCEDURE", "FUNCTION"):
                kind = self.advance().text.casefold()
                declarations.append(self._parse_oracle_routine(self.tokens[self.index - 1], kind))
            elif self.at_keyword("CURSOR"):
                declarations.append(self._parse_cursor_declaration())
            elif self.at_keyword("TYPE", "SUBTYPE", "PRAGMA"):
                declarations.append(self._parse_type_or_pragma())
            else:
                declarations.append(self._parse_variable_declaration())
            self.accept(TokenKind.TERMINATOR)
        return declarations

    def _parse_variable_declaration(self) -> ast.Declare:
        name = self.parse_identifier()
        if self.accept_keyword("EXCEPTION"):
            return ast.Declare(name=name, type=ast.DataType(name="EXCEPTION"))
        self.accept_keyword("CONSTANT")
        data_type = self.parse_data_type()
        if self.accept_keyword("NOT"):
            self.expect_keyword("NULL")
        default = None
        if self.accept(TokenKind.OPERATOR, ":=") or self.accept_keyword("DEFAULT"):
            default = self.parse_expr()
        return ast.Declare(name=name, type=data_type, default=default)

    def _parse_cursor_declaration(self) -> ast.CursorDecl:
        self.expect_keyword("CURSOR")
        name = self.parse_identifier()
        params: list[ast.RoutineParam] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                param_name = self.parse_identifier()
                param_type = self.parse_data_type()
                params.append(ast.RoutineParam(name=param_name, type=param_type))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        self.expect_keyword("IS", "AS")
        return ast.CursorDecl(name=name, query=self.parse_query(), options={"params": params})

    def _parse_type_or_pragma(self) -> ast.DialectStatement:
        start = self.advance()
        name = self.parse_identifier() if self._is_name() else None
        args: dict[str, Any] = {"name": name}
        if start.text.upper() == "TYPE":
            self.expect_keyword("IS")
            if self.accept_keyword("TABLE"):
                self.expect_keyword("OF")
                args["type_kind"] = "table"
                args["element_type"] = self.parse_data_type()
                if self.accept_keyword("INDEX"):
                    self.expect_keyword("BY")
                    args["index_type"] = self.parse_data_type()
            elif self.accept_keyword("VARRAY"):
                self.expect(TokenKind.PUNCT, "(")
                args["type_kind"] = "varray"
                args["limit"] = self.parse_expr()
                self.expect(TokenKind.PUNCT, ")")
                self.expect_keyword("OF")
                args["element_type"] = self.parse_data_type()
            elif self.accept_keyword("RECORD"):
                args["type_kind"] = "record"
                self.expect(TokenKind.PUNCT, "(")
                fields: list[ast.StructField] = []
                while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                    field_name = self.parse_identifier()
                    fields.append(ast.StructField(name=field_name, type=self.parse_data_type()))
                    if not self.accept(TokenKind.PUNCT, ","):
                        break
                self.expect(TokenKind.PUNCT, ")")
                args["fields"] = fields
            else:
                self.expect_keyword("REF")
                self.expect_keyword("CURSOR")
                args["type_kind"] = "ref_cursor"
            return ast.DialectStatement(
                name="type_declaration", args=args, span=self._token_span(start)
            )
        if start.text.upper() == "SUBTYPE":
            self.expect_keyword("IS")
            args["base_type"] = self.parse_data_type()
            if self.accept_keyword("RANGE"):
                args["range"] = ast.Range(self.parse_expr(), self.parse_expr())
            return ast.DialectStatement(
                name="subtype_declaration", args=args, span=self._token_span(start)
            )
        if self.accept(TokenKind.PUNCT, "("):
            args["args"] = (
                self.parse_comma_list(self.parse_expr) if not self.at(TokenKind.PUNCT, ")") else []
            )
            self.expect(TokenKind.PUNCT, ")")
        return ast.DialectStatement(name="pragma", args=args, span=self._token_span(start))

    def parse_if(self) -> ast.If:
        start = self.expect_keyword("IF")
        branches: list[tuple[ast.Node, list[ast.Node]]] = []
        condition = self.parse_expr()
        self.expect_keyword("THEN")
        branches.append((condition, self._parse_statement_sequence({"ELSIF", "ELSE", "END"})))
        while self.accept_keyword("ELSIF"):
            condition = self.parse_expr()
            self.expect_keyword("THEN")
            branches.append((condition, self._parse_statement_sequence({"ELSIF", "ELSE", "END"})))
        else_body = []
        if self.accept_keyword("ELSE"):
            else_body = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.expect_keyword("IF")
        return ast.If(branches=branches, else_=else_body, span=self._token_span(start))

    def parse_while(self) -> ast.While:
        start = self.expect_keyword("WHILE")
        condition = self.parse_expr()
        self.expect_keyword("LOOP")
        statements = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.expect_keyword("LOOP")
        if self._is_name():
            self.parse_identifier()
        return ast.While(condition=condition, statements=statements, span=self._token_span(start))

    def parse_loop(self) -> ast.Loop:
        start = self.expect_keyword("LOOP")
        statements = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.expect_keyword("LOOP")
        label = self.parse_identifier().value if self._is_name() else None
        return ast.Loop(label=label, statements=statements, span=self._token_span(start))

    def parse_for(self) -> ast.ForLoop:
        start = self.expect_keyword("FOR")
        variable = self.parse_identifier()
        self.expect_keyword("IN")
        if self.at_keyword("SELECT") or self.at_keyword("WITH"):
            iterable: ast.Query | ast.Range | ast.Node = self.parse_query()
        else:
            reverse = bool(self.accept_keyword("REVERSE"))
            lower = self.parse_expr(51)
            if self.accept(TokenKind.OPERATOR, ".."):
                iterable = ast.Range(lower, self.parse_expr(51), reverse=reverse)
            else:
                iterable = lower
        self.expect_keyword("LOOP")
        statements = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.expect_keyword("LOOP")
        if self._is_name():
            self.parse_identifier()
        return ast.ForLoop(variable, iterable, statements, span=self._token_span(start))

    def _parse_conditional_loop_control(self) -> ast.Node:
        start = self.advance()
        label = (
            self.parse_identifier().value
            if self._is_name() and not self.at_keyword("WHEN")
            else None
        )
        if self.accept_keyword("WHEN"):
            condition = self.parse_expr()
            action = (
                ast.Exit(label=label) if start.text.upper() == "EXIT" else ast.Continue(label=label)
            )
            return ast.If(branches=[(condition, [action])], span=self._token_span(start))
        return ast.Exit(label=label) if start.text.upper() == "EXIT" else ast.Continue(label=label)

    def _parse_case_statement(self) -> ast.DialectStatement:
        start = self.expect_keyword("CASE")
        operand = None if self.at_keyword("WHEN") else self.parse_expr()
        branches: list[dict[str, Any]] = []
        while self.accept_keyword("WHEN"):
            condition = self.parse_expr()
            self.expect_keyword("THEN")
            branches.append(
                {
                    "condition": condition,
                    "statements": self._parse_statement_sequence({"WHEN", "ELSE", "END"}),
                }
            )
        else_body: list[ast.Node] = []
        if self.accept_keyword("ELSE"):
            else_body = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.accept_keyword("CASE")
        return ast.DialectStatement(
            name="case_statement",
            args={"operand": operand, "branches": branches, "else": else_body},
            span=self._token_span(start),
        )

    def _parse_oracle_open(self) -> ast.Node:
        self.expect_keyword("OPEN")
        name = self.parse_identifier()
        if self.accept_keyword("FOR"):
            query = self.parse_query()
            return ast.DialectStatement(name="open_cursor_for", args={"name": name, "query": query})
        args = []
        if self.accept(TokenKind.PUNCT, "("):
            args = (
                self.parse_comma_list(self.parse_expr) if not self.at(TokenKind.PUNCT, ")") else []
            )
            self.expect(TokenKind.PUNCT, ")")
        return ast.OpenCursor(name=name, args=args)

    def _parse_oracle_fetch(self) -> OracleFetch:
        start = self.expect_keyword("FETCH")
        name = self.parse_identifier()
        bulk_collect = False
        if self.accept_keyword("BULK"):
            self.expect_keyword("COLLECT")
            bulk_collect = True
        into: list[ast.Identifier] = []
        if self.accept_keyword("INTO"):
            into = self.parse_comma_list(self.parse_identifier)
        limit = self.parse_expr() if self.accept_keyword("LIMIT") else None
        return OracleFetch(
            name=name,
            into=into,
            bulk_collect=bulk_collect,
            limit=limit,
            span=self._token_span(start),
        )

    def _parse_execute_immediate(self) -> ast.DynamicSql:
        start = self.expect_keyword("EXECUTE")
        self.expect_keyword("IMMEDIATE")
        sql_expr = self.parse_expr(70)
        into: list[ast.Identifier] = []
        using: list[ast.Node] = []
        if self.accept_keyword("INTO"):
            into = self.parse_comma_list(self.parse_identifier)
        if self.accept_keyword("USING"):
            while True:
                mode = "in"
                if self.accept_keyword("IN"):
                    mode = "in"
                    if self.accept_keyword("OUT"):
                        mode = "in out"
                elif self.accept_keyword("OUT"):
                    mode = "out"
                expr = self.parse_expr(31)
                using.append(ast.DialectExpr(name="using_arg", args={"mode": mode, "expr": expr}))
                if not self.accept(TokenKind.PUNCT, ","):
                    break
        parsed = self._parse_embedded_sql(sql_expr)
        return ast.DynamicSql(sql_expr, into, using, parsed, span=self._token_span(start))

    def _parse_forall(self) -> ast.DialectStatement:
        start = self.expect_keyword("FORALL")
        variable = self.parse_identifier()
        self.expect_keyword("IN")
        reverse = bool(self.accept_keyword("REVERSE"))
        lower = self.parse_expr(51)
        if not self.accept(TokenKind.OPERATOR, ".."):
            raise ParseError("Expected FORALL range.", self.peek())
        upper = self.parse_expr(51)
        save_exceptions = False
        if self.accept_keyword("SAVE"):
            self.expect_keyword("EXCEPTIONS")
            save_exceptions = True
        statement = self.parse_statement()
        return ast.DialectStatement(
            name="forall",
            args={
                "var": variable,
                "range": ast.Range(lower, upper, reverse=reverse),
                "save_exceptions": save_exceptions,
                "statement": statement,
            },
            span=self._token_span(start),
        )

    def _parse_oracle_table(self, start: Token, temporary: bool) -> ast.CreateTable:
        name = self.parse_qualified_name()
        columns: list[ast.ColumnDef] = []
        constraints: list[ast.TableConstraint] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                if self._is_constraint_start():
                    constraints.append(self._parse_table_constraint())
                else:
                    columns.append(self._parse_oracle_column_def())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        options: dict[str, Any] = {}
        while (
            not self.at_keyword("AS")
            and not self.at(TokenKind.TERMINATOR)
            and not self.at(TokenKind.EOF)
        ):
            if self.accept_keyword("TABLESPACE"):
                options["tablespace"] = self.parse_qualified_name()
            elif self.accept_keyword("PCTFREE", "PCTUSED", "INITRANS", "MAXTRANS"):
                key = self.tokens[self.index - 1].text.casefold()
                options[key] = self.parse_expr(80)
            elif self.accept_keyword("STORAGE"):
                options["storage"] = self._parse_oracle_option_group()
            elif self.accept_keyword("ORGANIZATION"):
                options["organization"] = self.advance().text.casefold()
            elif self.accept_keyword("ON"):
                self.expect_keyword("COMMIT")
                options["on_commit"] = self.advance().text.casefold()
                self.accept_keyword("ROWS")
            elif self.accept_keyword("PARTITION"):
                options["partition_by"] = self._parse_oracle_partition()
            elif self.accept_keyword("COMPRESS", "NOCOMPRESS", "LOGGING", "NOLOGGING"):
                key = self.tokens[self.index - 1].text.casefold()
                options[key] = (
                    self.parse_expr(80) if self._is_name() or self.at(TokenKind.NUMBER) else True
                )
            else:
                break
        as_query = self.parse_query() if self.accept_keyword("AS") else None
        return ast.CreateTable(
            name=name,
            columns=columns,
            constraints=constraints,
            as_query=as_query,
            temporary=temporary,
            options=options,
            span=self._token_span(start),
        )

    def _parse_oracle_column_def(self) -> ast.ColumnDef:
        name = self.parse_identifier()
        data_type = self.parse_data_type()
        constraints: list[ast.ColumnConstraint] = []
        default = None
        identity = None
        while (
            not self.at(TokenKind.PUNCT, ",")
            and not self.at(TokenKind.PUNCT, ")")
            and not self.at(TokenKind.EOF)
        ):
            if self.accept_keyword("CONSTRAINT"):
                constraint_name = self.parse_identifier()
                if self.accept_keyword("DEFAULT"):
                    default = self._parse_default_expr()
                    constraints.append(
                        ast.ColumnConstraint("default", name=constraint_name, expr=default)
                    )
                elif self.accept_keyword("NOT"):
                    self.expect_keyword("NULL")
                    constraints.append(ast.ColumnConstraint("not_null", name=constraint_name))
                elif self.accept_keyword("PRIMARY"):
                    self.expect_keyword("KEY")
                    constraints.append(ast.ColumnConstraint("primary_key", name=constraint_name))
                elif self.accept_keyword("REFERENCES"):
                    table = self.parse_qualified_name()
                    cols = (
                        self._parse_identifier_list_in_parens()
                        if self.at(TokenKind.PUNCT, "(")
                        else []
                    )
                    constraints.append(
                        ast.ColumnConstraint(
                            "foreign_key", name=constraint_name, ref_table=table, ref_columns=cols
                        )
                    )
                else:
                    constraints.append(ast.ColumnConstraint("named", name=constraint_name))
            elif self.accept_keyword("DEFAULT"):
                default = self._parse_default_expr()
            elif self.accept_keyword("NOT"):
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
                cols = (
                    self._parse_identifier_list_in_parens() if self.at(TokenKind.PUNCT, "(") else []
                )
                constraints.append(
                    ast.ColumnConstraint("foreign_key", ref_table=table, ref_columns=cols)
                )
            elif self.accept_keyword("CHECK"):
                self.expect(TokenKind.PUNCT, "(")
                expr = self.parse_expr()
                self.expect(TokenKind.PUNCT, ")")
                constraints.append(ast.ColumnConstraint("check", expr=expr))
            elif self.accept_keyword("GENERATED"):
                self.accept_keyword("ALWAYS")
                self.expect_keyword("AS")
                self.expect_keyword("IDENTITY")
                identity = ast.DialectExpr(name="identity", args={})
            else:
                break
        return ast.ColumnDef(
            name=name, type=data_type, constraints=constraints, default=default, identity=identity
        )

    def _parse_default_expr(self) -> ast.Node:
        if self.accept(TokenKind.PUNCT, "("):
            value = self.parse_expr()
            self.expect(TokenKind.PUNCT, ")")
            return value
        return self.parse_expr(31)

    def _parse_oracle_option_group(self) -> ast.DialectExpr:
        self.expect(TokenKind.PUNCT, "(")
        options: dict[str, ast.Node] = {}
        while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
            key = self.advance().text.casefold()
            options[key] = self.parse_expr(80)
            if not self.accept(TokenKind.PUNCT, ","):
                break
        self.expect(TokenKind.PUNCT, ")")
        return ast.DialectExpr(name="storage", args=options)

    def _parse_oracle_partition(self) -> ast.DialectExpr:
        self.expect_keyword("BY")
        method = self.advance().text.casefold()
        self.expect(TokenKind.PUNCT, "(")
        columns = self.parse_comma_list(self.parse_identifier)
        self.expect(TokenKind.PUNCT, ")")
        partitions: list[ast.Node] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                self.accept_keyword("PARTITION")
                partition_name = self.parse_identifier()
                self.expect_keyword("VALUES")
                self.expect_keyword("LESS")
                self.expect_keyword("THAN")
                self.expect(TokenKind.PUNCT, "(")
                values = self.parse_comma_list(self.parse_expr)
                self.expect(TokenKind.PUNCT, ")")
                partitions.append(
                    ast.DialectExpr(
                        name="partition", args={"name": partition_name, "values": values}
                    )
                )
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        return ast.DialectExpr(
            name="partition_by",
            args={"method": method, "columns": columns, "partitions": partitions},
        )

    def parse_select(self) -> OracleSelect:
        start = self.expect_keyword("SELECT")
        distinct = bool(self.accept_keyword("DISTINCT"))
        if not distinct:
            self.accept_keyword("ALL")
        projections = self.parse_comma_list(self._parse_oracle_select_item)
        bulk_collect = False
        into_list: list[ast.Node] = []
        if self.accept_keyword("BULK"):
            self.expect_keyword("COLLECT")
            bulk_collect = True
        if self.accept_keyword("INTO"):
            into_list = self.parse_comma_list(self.parse_expr)
        from_items = self._parse_from_list() if self.accept_keyword("FROM") else []
        where = self.parse_expr() if self.accept_keyword("WHERE") else None
        hierarchical = self._parse_hierarchical_clause()
        group_by = self._parse_group_by() if self.accept_keyword("GROUP") else None
        having = self.parse_expr() if self.accept_keyword("HAVING") else None
        windows = {}
        if self.accept_keyword("WINDOW"):
            while True:
                name = self.parse_identifier()
                self.expect_keyword("AS")
                windows[name.value] = self.parse_window_spec()
                if not self.accept(TokenKind.PUNCT, ","):
                    break
        qualify = self.parse_expr() if self.accept_keyword("QUALIFY") else None
        return OracleSelect(
            distinct=distinct,
            projections=projections,
            into_list=into_list,
            bulk_collect=bulk_collect,
            from_=from_items,
            where=where,
            group_by=group_by,
            having=having,
            windows=windows,
            qualify=qualify,
            hierarchical=hierarchical,
            span=self._token_span(start),
        )

    def _parse_oracle_select_item(self) -> ast.SelectItem:
        start = self.peek()
        expr = self.parse_expr()
        alias = None
        if self.accept_keyword("AS"):
            alias = self.parse_identifier()
        elif not self.at_keyword("BULK") and self._can_be_alias():
            alias = self.parse_identifier()
        return ast.SelectItem(expr, alias, span=self._token_span(start))

    def _parse_hierarchical_clause(self) -> ast.DialectExpr | None:
        start_with = None
        condition = None
        nocycle = False
        while True:
            if self.accept_keyword("START"):
                self.expect_keyword("WITH")
                start_with = self.parse_expr()
            elif self.accept_keyword("CONNECT"):
                self.expect_keyword("BY")
                nocycle = bool(self.accept_keyword("NOCYCLE"))
                condition = self.parse_expr()
            else:
                break
        if start_with is None and condition is None:
            return None
        return ast.DialectExpr(
            name="connect_by",
            args={
                "start_with": start_with,
                "condition": condition,
                "nocycle": ast.Literal("boolean", str(nocycle).lower()),
            },
        )

    def _parse_from_item(self) -> ast.Node:
        source = super()._parse_from_item()
        if self.accept_keyword("PIVOT") or self.at_keyword("UNPIVOT"):
            token = (
                self.tokens[self.index - 1]
                if self.tokens[self.index - 1].text.upper() == "PIVOT"
                else self.advance()
            )
            pivot = token.text.casefold()
            include_nulls = bool(self.accept_keyword("INCLUDE"))
            if include_nulls:
                self.expect_keyword("NULLS")
            elif self.accept_keyword("EXCLUDE"):
                self.expect_keyword("NULLS")
            self.expect(TokenKind.PUNCT, "(")
            aggregates = self.parse_comma_list(self._parse_select_item)
            self.expect_keyword("FOR")
            pivot_columns = (
                self._parse_identifier_list()
                if self.accept(TokenKind.PUNCT, "(")
                else [self.parse_identifier()]
            )
            self.expect_keyword("IN")
            self.expect(TokenKind.PUNCT, "(")
            values = self.parse_comma_list(self._parse_select_item)
            self.expect(TokenKind.PUNCT, ")")
            self.expect(TokenKind.PUNCT, ")")
            alias = self.parse_identifier() if self._can_be_alias() else None
            return ast.DialectExpr(
                name=pivot,
                args={
                    "source": source,
                    "aggregates": aggregates,
                    "for": pivot_columns,
                    "in": values,
                    "alias": alias,
                    "include_nulls": ast.Literal("boolean", str(include_nulls).lower()),
                },
            )
        return source

    def parse_merge(self) -> ast.Merge:
        start = self.expect_keyword("MERGE")
        self.expect_keyword("INTO")
        target = self.parse_qualified_name()
        alias = self._parse_optional_alias()
        self.expect_keyword("USING")
        source = self._parse_from_item()
        self.expect_keyword("ON")
        self.accept(TokenKind.PUNCT, "(")
        condition = self.parse_expr()
        self.accept(TokenKind.PUNCT, ")")
        clauses: list[ast.MergeClause] = []
        while self.accept_keyword("WHEN"):
            matched = bool(self.accept_keyword("MATCHED"))
            if not matched:
                self.expect_keyword("NOT")
                self.expect_keyword("MATCHED")
            clause_condition = self.parse_expr() if self.accept_keyword("AND") else None
            self.expect_keyword("THEN")
            if self.accept_keyword("UPDATE"):
                self.expect_keyword("SET")
                assignments = self.parse_comma_list(self._parse_assignment)
                update_where = self.parse_expr() if self.accept_keyword("WHERE") else None
                delete_where = None
                if self.accept_keyword("DELETE"):
                    self.expect_keyword("WHERE")
                    delete_where = self.parse_expr()
                update = ast.Update(target=target, assignments=assignments, where=update_where)
                action: ast.Node = (
                    ast.DialectStatement(
                        name="oracle_merge_update",
                        args={"update": update, "delete_where": delete_where},
                    )
                    if delete_where is not None
                    else update
                )
            elif self.accept_keyword("INSERT"):
                columns = (
                    self._parse_identifier_list_in_parens() if self.at(TokenKind.PUNCT, "(") else []
                )
                values = self.parse_values()
                insert = ast.Insert(target=target, columns=columns, source=values)
                if self.accept_keyword("WHERE"):
                    action = ast.DialectStatement(
                        name="oracle_merge_insert",
                        args={"insert": insert, "where": self.parse_expr()},
                    )
                else:
                    action = insert
            else:
                raise ParseError("Expected Oracle MERGE action.", self.peek())
            clauses.append(ast.MergeClause(matched, clause_condition, action))
        return ast.Merge(
            target=target,
            alias=alias,
            source=source,
            on=condition,
            clauses=clauses,
            span=self._token_span(start),
        )

    def _parse_oracle_materialized_view(self, start: Token, replace: bool) -> ast.CreateView:
        name = self.parse_qualified_name()
        options: dict[str, Any] = {}
        while not self.at_keyword("AS") and not self.at(TokenKind.EOF):
            if self.accept_keyword("BUILD"):
                options["build"] = self.advance().text.casefold()
            elif self.accept_keyword("REFRESH"):
                refresh = self.advance().text.casefold()
                mode = None
                if self.accept_keyword("ON"):
                    mode = self.advance().text.casefold()
                options["refresh"] = ast.DialectExpr(
                    name="refresh", args={"method": refresh, "mode": mode}
                )
            elif self.accept_keyword("ENABLE"):
                self.expect_keyword("QUERY")
                self.expect_keyword("REWRITE")
                options["query_rewrite"] = True
            else:
                break
        self.expect_keyword("AS")
        return ast.CreateView(
            name=name,
            query=self.parse_query(),
            materialized=True,
            replace=replace,
            options=options,
            span=self._token_span(start),
        )

    def _parse_oracle_sequence(self, start: Token) -> ast.CreateSequence:
        return ast.CreateSequence(name=self.parse_qualified_name(), span=self._token_span(start))

    def _parse_oracle_routine(
        self, start: Token, routine_kind: str, *, replace: bool = False
    ) -> ast.CreateRoutine:
        name = self.parse_qualified_name()
        params: list[ast.RoutineParam] = []
        if self.accept(TokenKind.PUNCT, "("):
            while not self.at(TokenKind.PUNCT, ")") and not self.at(TokenKind.EOF):
                params.append(self._parse_oracle_param())
                if not self.accept(TokenKind.PUNCT, ","):
                    break
            self.expect(TokenKind.PUNCT, ")")
        returns = None
        if routine_kind == "function":
            self.expect_keyword("RETURN")
            returns = self.parse_data_type()
        options: dict[str, Any] = {"replace": replace} if replace else {}
        while self.at_keyword("AUTHID", "DETERMINISTIC", "PIPELINED", "RESULT_CACHE"):
            key = self.advance().text.casefold()
            if key == "authid":
                options[key] = self.advance().text.casefold()
            else:
                options[key] = True
        if not (self.accept_keyword("IS") or self.accept_keyword("AS")):
            options["forward"] = True
            return ast.CreateRoutine(
                routine_kind,
                name,
                params,
                returns,
                body=None,
                options=options,
                span=self._token_span(start),
            )
        declarations = self._parse_oracle_declarations({"BEGIN"})
        if self.at_keyword("BEGIN"):
            body = self.parse_block()
            body.declarations = declarations + body.declarations
        else:
            body = ast.Block(declarations=declarations)
        return ast.CreateRoutine(
            routine_kind,
            name,
            params,
            returns,
            body=body,
            options=options,
            span=self._token_span(start),
        )

    def _parse_oracle_param(self) -> ast.RoutineParam:
        name = self.parse_identifier()
        mode = None
        if self.accept_keyword("IN"):
            mode = "in"
            if self.accept_keyword("OUT"):
                mode = "in out"
        elif self.accept_keyword("OUT"):
            mode = "out"
        self.accept_keyword("NOCOPY")
        data_type = self.parse_data_type()
        default = None
        if self.accept(TokenKind.OPERATOR, ":=") or self.accept_keyword("DEFAULT"):
            default = self.parse_expr(31)
        return ast.RoutineParam(name=name, mode=mode, type=data_type, default=default)

    def _parse_oracle_package(
        self, start: Token, package_body: bool, *, replace: bool = False
    ) -> ast.CreateRoutine:
        name = self.parse_qualified_name()
        options: dict[str, Any] = {}
        if self.accept_keyword("AUTHID"):
            options["authid"] = self.advance().text.casefold()
        if replace:
            options["replace"] = True
        self.expect_keyword("IS", "AS")
        declarations = self._parse_oracle_declarations({"BEGIN", "END"})
        if self.at_keyword("BEGIN"):
            body = self.parse_block()
            body.declarations = declarations + body.declarations
        else:
            self.expect_keyword("END")
            if self._is_name():
                self.parse_identifier()
            body = ast.Block(declarations=declarations)
        return ast.CreateRoutine(
            routine_kind="package_body" if package_body else "package",
            name=name,
            body=body,
            options=options,
            span=self._token_span(start),
        )

    def _parse_oracle_trigger(self, start: Token, replace: bool) -> ast.CreateRoutine:
        name = self.parse_qualified_name()
        timing = "before"
        if self.accept_keyword("AFTER"):
            timing = "after"
        elif self.accept_keyword("INSTEAD"):
            self.expect_keyword("OF")
            timing = "instead_of"
        events: list[dict[str, Any]] = []
        while not self.at_keyword("ON") and not self.at(TokenKind.EOF):
            if self.accept_keyword("INSERT", "UPDATE", "DELETE"):
                event = self.tokens[self.index - 1].text.casefold()
                columns = []
                if event == "update" and self.accept_keyword("OF"):
                    columns = self.parse_comma_list(self.parse_identifier)
                events.append({"event": event, "columns": columns})
            elif not self.accept_keyword("OR"):
                self.advance()
        self.expect_keyword("ON")
        trigger_on = None
        if self.accept_keyword("DATABASE"):
            trigger_on = None
        else:
            trigger_on = self.parse_qualified_name()
        referencing: dict[str, ast.Identifier] = {}
        if self.accept_keyword("REFERENCING"):
            while self.at_keyword("NEW", "OLD"):
                key = self.advance().text.casefold()
                self.accept_keyword("AS")
                referencing[key] = self.parse_identifier()
        for_each_row = False
        if self.accept_keyword("FOR"):
            self.expect_keyword("EACH")
            self.expect_keyword("ROW")
            for_each_row = True
        when = None
        if self.accept_keyword("WHEN"):
            self.expect(TokenKind.PUNCT, "(")
            when = self.parse_expr()
            self.expect(TokenKind.PUNCT, ")")
        declarations = []
        if self.accept_keyword("DECLARE"):
            declarations = self._parse_oracle_declarations({"BEGIN"})
        self.expect_keyword("BEGIN")
        self.index -= 1
        body = self.parse_block()
        body.declarations = declarations + body.declarations
        return ast.CreateRoutine(
            routine_kind="trigger",
            name=name,
            body=body,
            options={
                "replace": replace,
                "timing": timing,
                "events": events,
                "trigger_on": trigger_on,
                "for_each_row": for_each_row,
                "when": when,
                "referencing": referencing,
            },
            span=self._token_span(start),
        )

    def parse_sequence(self) -> ast.Node | None:
        return None

    def parse_create(self) -> ast.Node:
        # Keep sequence options in the shared node rather than falling through to raw parsing.
        return self._parse_create_with_sequence()

    def _parse_create_with_sequence(self) -> ast.Node:
        start_index = self.index
        start = self.peek()
        node = self._parse_create_non_sequence()
        if node is not None:
            return node
        self.index = start_index
        self.expect_keyword("CREATE")
        self.accept_keyword("OR") and self.accept_keyword("REPLACE")
        self.expect_keyword("SEQUENCE")
        name = self.parse_qualified_name()
        options: dict[str, Any] = {}
        while not self.at(TokenKind.EOF) and not self.at(TokenKind.TERMINATOR):
            if self.accept_keyword("START", "INCREMENT", "MINVALUE", "MAXVALUE", "CACHE"):
                key = self.tokens[self.index - 1].text.casefold()
                if key == "start":
                    self.accept_keyword("WITH")
                    key = "start_with"
                elif key == "increment":
                    self.expect_keyword("BY")
                    key = "increment_by"
                options[key] = self.parse_expr(80)
            elif self.accept_keyword("NOCACHE", "NOCYCLE", "NOORDER", "NOMAXVALUE", "NOMINVALUE"):
                options[self.tokens[self.index - 1].text.casefold()] = True
            elif self.accept_keyword("CYCLE", "ORDER"):
                options[self.tokens[self.index - 1].text.casefold()] = True
            else:
                break
        return ast.CreateSequence(name=name, options=options, span=self._token_span(start))

    def _parse_create_non_sequence(self) -> ast.Node | None:
        start_index = self.index
        self.expect_keyword("CREATE")
        if self.accept_keyword("OR"):
            self.expect_keyword("REPLACE")
        if self.at_keyword("SEQUENCE"):
            self.index = start_index
            return None
        self.index = start_index
        # parse_create's non-sequence branch is kept separate to avoid consuming sequence options.
        return self._parse_create_body()

    def _parse_create_body(self) -> ast.Node:
        start_index = self.index
        start = self.expect_keyword("CREATE")
        replace = False
        if self.accept_keyword("OR"):
            self.expect_keyword("REPLACE")
            replace = True
        editionable = bool(self.accept_keyword("EDITIONABLE"))
        force = bool(self.accept_keyword("FORCE"))
        temporary = False
        if self.accept_keyword("GLOBAL"):
            self.expect_keyword("TEMPORARY")
            temporary = True
        public = bool(self.accept_keyword("PUBLIC"))
        unique = bool(self.accept_keyword("UNIQUE"))
        bitmap = bool(self.accept_keyword("BITMAP"))
        if self.accept_keyword("TABLE"):
            return self._parse_oracle_table(start, temporary)
        if self.accept_keyword("MATERIALIZED"):
            self.expect_keyword("VIEW")
            return self._parse_oracle_materialized_view(start, replace)
        if self.accept_keyword("VIEW"):
            name = self.parse_qualified_name()
            columns = self._parse_identifier_list() if self.accept(TokenKind.PUNCT, "(") else []
            self.expect_keyword("AS")
            query = self.parse_query()
            options: dict[str, Any] = {"force": force}
            if editionable:
                options["editionable"] = True
            if self.accept_keyword("WITH"):
                if self.accept_keyword("READ"):
                    self.expect_keyword("ONLY")
                    options["read_only"] = True
                else:
                    self.expect_keyword("CHECK")
                    self.expect_keyword("OPTION")
                    options["check_option"] = True
            return ast.CreateView(
                name=name,
                columns=columns,
                query=query,
                replace=replace,
                options=options,
                span=self._token_span(start),
            )
        if self.accept_keyword("SYNONYM"):
            name = self.parse_qualified_name()
            self.expect_keyword("FOR")
            return ast.CreateSynonym(
                name=name,
                target=self.parse_qualified_name(),
                public=public,
                span=self._token_span(start),
            )
        if unique or bitmap:
            self.expect_keyword("INDEX")
            name = self.parse_qualified_name()
            self.expect_keyword("ON")
            table = self.parse_qualified_name()
            columns = self._parse_identifier_list_in_parens()
            return ast.CreateIndex(
                name=name,
                table=table,
                columns=columns,
                unique=unique,
                options={"bitmap": bitmap} if bitmap else {},
                span=self._token_span(start),
            )
        if self.accept_keyword("INDEX"):
            name = self.parse_qualified_name()
            self.expect_keyword("ON")
            table = self.parse_qualified_name()
            columns = self._parse_identifier_list_in_parens()
            return ast.CreateIndex(
                name=name, table=table, columns=columns, span=self._token_span(start)
            )
        if self.accept_keyword("DATABASE"):
            self.expect_keyword("LINK")
            name = self.parse_qualified_name()
            options: dict[str, Any] = {}
            if self.accept_keyword("CONNECT"):
                self.expect_keyword("TO")
                options["user"] = self.parse_identifier()
                self.expect_keyword("IDENTIFIED")
                self.expect_keyword("BY")
                if self.at(TokenKind.STRING):
                    self.advance()
                options["password"] = "***"
            if self.accept_keyword("USING"):
                options["using"] = self.parse_expr(80)
            return ast.DialectStatement(
                name="create_database_link",
                args={"object_type": "database_link", "name": name, "options": options},
                span=self._token_span(start),
            )
        if self.accept_keyword("USER"):
            name = self.parse_qualified_name()
            options: dict[str, Any] = {}
            if self.accept_keyword("IDENTIFIED"):
                self.expect_keyword("BY")
                if self.at(TokenKind.STRING):
                    self.advance()
                options["password"] = "***"
            return ast.CreatePrincipal("user", name, options, span=self._token_span(start))
        if self.accept_keyword("ROLE"):
            return ast.CreatePrincipal(
                "role", self.parse_qualified_name(), span=self._token_span(start)
            )
        if self.accept_keyword("PACKAGE"):
            return self._parse_oracle_package(
                start, bool(self.accept_keyword("BODY")), replace=replace
            )
        if self.accept_keyword("PROCEDURE"):
            return self._parse_oracle_routine(start, "procedure", replace=replace)
        if self.accept_keyword("FUNCTION"):
            return self._parse_oracle_routine(start, "function", replace=replace)
        if self.accept_keyword("TRIGGER"):
            return self._parse_oracle_trigger(start, replace)
        self.index = start_index
        return super().parse_create()


__all__ = ["Parser"]
