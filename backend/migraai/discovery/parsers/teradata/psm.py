from __future__ import annotations

from typing import Any

from migraai.discovery import ast
from migraai.discovery.lexer import TokenKind
from migraai.discovery.parser_base import ParseError, ParseLimitError


class PsmMixin:
    def parse_statement_ext(self) -> ast.Node | None:
        if self._is_name() and self.peek(1).text == ":":
            label = self.parse_identifier().value
            self.advance()
            node = self.parse_statement()
            if isinstance(node, (ast.Block, ast.Loop)):
                node.label = label
            return node
        if self.at_keyword("GET") and self.at_keyword("DIAGNOSTICS", 1):
            return self._parse_get_diagnostics()
        if self.at_keyword("CASE"):
            return self._parse_case_statement()
        if self.at_keyword("SIGNAL") or self.at_keyword("RESIGNAL"):
            return self._parse_signal()
        if self.at_keyword("ITERATE"):
            self.advance()
            return ast.Continue(label=self.parse_identifier().value)
        if self.at_keyword("LEAVE"):
            self.advance()
            return ast.Leave(label=self.parse_identifier().value)
        if self.at_keyword("FOR") and self.at_keyword("AS", 2):
            return self._parse_cursor_for()
        return super().parse_statement_ext()

    def parse_loop(self) -> ast.Loop:
        loop = super().parse_loop()
        self._consume_loop_end_label()
        return loop

    def parse_while(self) -> ast.While:
        loop = super().parse_while()
        self._consume_loop_end_label()
        return loop

    def parse_for(self) -> ast.ForLoop:
        loop = super().parse_for()
        self._consume_loop_end_label()
        return loop

    def parse_repeat(self) -> ast.Repeat:
        loop = super().parse_repeat()
        self._consume_loop_end_label()
        return loop

    def _consume_loop_end_label(self) -> None:
        if self._is_name() and (
            self.peek(1).kind in {TokenKind.TERMINATOR, TokenKind.EOF} or self.at_keyword("END", 1)
        ):
            self.advance()

    def parse_primary_ext(self) -> ast.Node | None:
        if self.at_keyword("SUBSTRING", "SUBSTR"):
            token = self.advance()
            self.expect(TokenKind.PUNCT, "(")
            first = self.parse_expr()
            args: list[ast.Node] = [first]
            if self.accept_keyword("FROM"):
                args.append(ast.NamedArg(ast.Identifier(value="from"), self.parse_expr()))
                if self.accept_keyword("FOR"):
                    args.append(ast.NamedArg(ast.Identifier(value="for"), self.parse_expr()))
            elif self.accept(TokenKind.PUNCT, ","):
                args.append(ast.NamedArg(ast.Identifier(value="from"), self.parse_expr()))
                if self.accept(TokenKind.PUNCT, ","):
                    args.append(ast.NamedArg(ast.Identifier(value="for"), self.parse_expr()))
            self.expect(TokenKind.PUNCT, ")")
            return ast.FunctionCall(
                ast.QualifiedName(parts=[ast.Identifier(value=token.text.upper())]),
                args,
                span=self._token_span(token),
            )
        if self.at_keyword("POSITION"):
            token = self.advance()
            self.expect(TokenKind.PUNCT, "(")
            first = self.parse_expr(31)
            self.expect_keyword("IN")
            second = self.parse_expr()
            self.expect(TokenKind.PUNCT, ")")
            return ast.FunctionCall(
                ast.QualifiedName(parts=[ast.Identifier(value="POSITION")]),
                [first, ast.NamedArg(ast.Identifier(value="in"), second)],
                span=self._token_span(token),
            )
        if self.at_keyword("TRIM"):
            token = self.advance()
            self.expect(TokenKind.PUNCT, "(")
            spec = None
            if self.at_keyword("BOTH", "LEADING", "TRAILING"):
                spec = self.advance().text.casefold()
            first = self.parse_expr()
            if self.accept_keyword("FROM"):
                source = self.parse_expr()
                args = []
                if spec:
                    args.append(
                        ast.NamedArg(
                            ast.Identifier(value="trim_spec"),
                            ast.Literal(type="string", value=spec),
                        )
                    )
                args.extend(
                    [
                        ast.NamedArg(ast.Identifier(value="trim_char"), first),
                        ast.NamedArg(ast.Identifier(value="from"), source),
                    ]
                )
            else:
                args = [first]
            self.expect(TokenKind.PUNCT, ")")
            return ast.FunctionCall(
                ast.QualifiedName(parts=[ast.Identifier(value="TRIM")]),
                args,
                span=self._token_span(token),
            )
        return super().parse_primary_ext()

    def parse_block(self) -> ast.Block:
        start = self.expect_keyword("BEGIN")
        atomic = not (self.accept_keyword("NOT") and self.expect_keyword("ATOMIC"))
        if atomic:
            self.accept_keyword("ATOMIC")
        declarations: list[ast.Node] = []
        while self.at_keyword("DECLARE"):
            self.advance()
            declaration = self._parse_declare_after_keyword()
            declarations.extend(declaration if isinstance(declaration, list) else [declaration])
            self.accept(TokenKind.TERMINATOR)
        statements: list[ast.Node] = []
        while not self.at_keyword("END") and not self.at(TokenKind.EOF):
            if self.at_keyword("DECLARE"):
                self.advance()
                declaration = self._parse_declare_after_keyword()
                declarations.extend(declaration if isinstance(declaration, list) else [declaration])
                self.accept(TokenKind.TERMINATOR)
                continue
            if self.at(TokenKind.TERMINATOR):
                self.advance()
                continue
            statement_start = self.index
            try:
                statements.append(self.parse_statement())
            except (ParseError, ParseLimitError) as error:
                if isinstance(error, ParseLimitError) and error.code == "parser.time_limit":
                    raise
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
        self.expect_keyword("END")
        self.accept_keyword("IF", "LOOP", "WHILE", "REPEAT", "FOR")
        label = self.parse_identifier().value if self._is_name() else None
        block = ast.Block(
            label=label,
            declarations=declarations,
            statements=statements,
            span=self._token_span(start),
        )
        if not atomic:
            block.span = self._token_span(start)
        return block

    def _parse_declare_after_keyword(self) -> ast.Node | list[ast.Node]:
        if self.at_keyword("CONTINUE", "EXIT", "UNDO"):
            action = self.advance().text.casefold()
            self.expect_keyword("HANDLER")
            self.expect_keyword("FOR")
            conditions: list[str] = []
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
                    "SIGNAL",
                    "RESIGNAL",
                    "IF",
                    "WHILE",
                    "LOOP",
                    "FOR",
                    "CASE",
                )
                and not self.at(TokenKind.TERMINATOR)
                and not self.at(TokenKind.EOF)
            ):
                if self.accept(TokenKind.PUNCT, ","):
                    continue
                conditions.append(self.advance().text)
            statements = (
                self.parse_block().statements
                if self.at_keyword("BEGIN")
                else [self.parse_statement()]
                if not self._statement_end()
                else []
            )
            return ast.HandlerDecl(f"{action} {' '.join(conditions)}", statements)
        if self._is_name() and self.at_keyword("CONDITION", 1):
            name = self.parse_identifier()
            self.advance()
            self.expect_keyword("FOR")
            self.expect_keyword("SQLSTATE")
            value = self.expect(TokenKind.STRING).value
            return ast.Declare(
                name,
                ast.DataType(name="CONDITION"),
                ast.Literal(type="string", value=value),
            )
        if self._is_name() and self.at_keyword("CURSOR", 1):
            name = self.parse_identifier()
            self.advance()
            options: dict[str, Any] = {}
            if self.accept_keyword("WITH"):
                if self.accept_keyword("HOLD"):
                    options["with_hold"] = True
                elif self.accept_keyword("RETURN"):
                    options["with_return"] = True
                    if self.accept_keyword("TO"):
                        options["return_to"] = self.advance().text.casefold()
            self.expect_keyword("FOR")
            return ast.CursorDecl(name, self.parse_query(), options)
        names = [self.parse_identifier()]
        while self.accept(TokenKind.PUNCT, ","):
            names.append(self.parse_identifier())
        datatype = self.parse_data_type()
        default = None
        if self.accept_keyword("DEFAULT", "VALUE") or self.accept(TokenKind.OPERATOR, ":="):
            default = self.parse_expr()
        elif self.accept(TokenKind.OPERATOR, "="):
            default = self.parse_expr()
        return [ast.Declare(name, datatype, default) for name in names]

    def _parse_cursor_for(self) -> ast.ForLoop:
        start = self.expect_keyword("FOR")
        variable = self.parse_identifier()
        self.expect_keyword("AS")
        if self._is_name() and not self.at_keyword("CURSOR"):
            self.parse_identifier()
        if self.accept_keyword("CURSOR"):
            if self.accept_keyword("WITH"):
                self.expect_keyword("HOLD")
            self.expect_keyword("FOR")
        query = self.parse_query()
        self.expect_keyword("DO")
        body = self._parse_statement_sequence({"END"})
        self.expect_keyword("END")
        self.expect_keyword("FOR")
        self._consume_loop_end_label()
        return ast.ForLoop(variable, query, body, span=self._token_span(start))

    def _parse_signal(self) -> ast.Node:
        resignal = bool(self.accept_keyword("RESIGNAL"))
        if not resignal:
            self.expect_keyword("SIGNAL")
        condition = None
        if self.accept_keyword("SQLSTATE"):
            self.accept_keyword("VALUE")
            condition = self.expect(TokenKind.STRING).value
        elif self._is_name():
            condition = self.parse_identifier().value
        message = None
        if self.accept_keyword("SET"):
            self.expect_keyword("MESSAGE_TEXT")
            self.expect(TokenKind.OPERATOR, "=")
            message = self.parse_expr()
        return ast.Signal(condition=condition, expr=message)

    def _parse_get_diagnostics(self) -> ast.DialectStatement:
        self.expect_keyword("GET")
        self.expect_keyword("DIAGNOSTICS")
        exception = bool(self.accept_keyword("EXCEPTION"))
        condition = self.parse_expr(80) if exception else None
        target = self.parse_expr(70)
        self.expect(TokenKind.OPERATOR, "=")
        item = self.parse_identifier()
        return ast.DialectStatement(
            "get_diagnostics",
            {"exception": exception, "condition": condition, "target": target, "item": item},
        )

    def _parse_case_statement(self) -> ast.DialectStatement:
        self.expect_keyword("CASE")
        operand = None if self.at_keyword("WHEN") else self.parse_expr()
        whens = []
        while self.accept_keyword("WHEN"):
            condition = self.parse_expr()
            self.expect_keyword("THEN")
            statements = self._parse_statement_sequence({"WHEN", "ELSE", "END"})
            whens.append(
                ast.DialectExpr("case_when", {"condition": condition, "statements": statements})
            )
        else_statements = (
            self._parse_statement_sequence({"END"}) if self.accept_keyword("ELSE") else []
        )
        self.expect_keyword("END")
        self.accept_keyword("CASE")
        return ast.DialectStatement(
            "case_statement",
            {"operand": operand, "whens": whens, "else": else_statements},
        )
