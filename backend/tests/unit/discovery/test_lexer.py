from migraai.discovery.ast import Span
from migraai.discovery.lexer import Lexer, LexerConfig, TokenKind


def test_quotes_comments_and_nested_comments_keep_spans() -> None:
    lexer = Lexer(
        "select q'[a''b]', \"a\"\"b\" /* one /* two */ end */ -- tail\n",
        LexerConfig(oracle_q_quotes=True, nested_comments=True),
    )
    tokens = lexer.tokenize()

    assert [token.kind for token in tokens[:-1]] == [
        TokenKind.IDENT,
        TokenKind.STRING,
        TokenKind.PUNCT,
        TokenKind.QUOTED_IDENT,
    ]
    assert tokens[1].value == "a''b"
    assert tokens[3].value == 'a"b'
    assert [comment.text for comment in lexer.comments] == [
        "/* one /* two */ end */",
        "-- tail",
    ]
    start = lexer.text.index("/*") + 1
    assert lexer.comments[0].span == Span(1, start, 1, start + len(lexer.comments[0].text))
    assert not lexer.issues


def test_dollar_body_and_all_parameter_styles() -> None:
    lexer = Lexer(
        "do $tag$select :name$tag$ :name, ?, $1, @p, @@x, &v, ${hivevar:db}",
        LexerConfig(dollar_quoting=True),
    )
    tokens = lexer.tokenize()

    assert tokens[1].kind is TokenKind.DOLLAR_BODY
    assert tokens[1].value == "select :name"
    params = [token for token in tokens[2:-1] if token.kind is TokenKind.PARAM]
    assert [token.text for token in params] == [
        ":name",
        "?",
        "$1",
        "@p",
        "@@x",
        "&v",
        "${hivevar:db}",
    ]
    assert tokens[-1].kind is TokenKind.EOF
    assert not lexer.issues


def test_batch_separator_dot_command_and_unknown_character() -> None:
    lexer = Lexer(
        "select 1\nGO\n.RUN FILE.SQL\nselect §",
        LexerConfig(batch_separators=(r"GO",)),
    )
    tokens = lexer.tokenize()

    assert [token.kind for token in tokens] == [
        TokenKind.IDENT,
        TokenKind.NUMBER,
        TokenKind.TERMINATOR,
        TokenKind.DOT_COMMAND,
        TokenKind.IDENT,
        TokenKind.OPERATOR,
        TokenKind.EOF,
    ]
    assert tokens[2].value == "GO"
    assert tokens[3].value == ".RUN FILE.SQL"
    assert lexer.issues[0].code == "lexer.unknown_character"


def test_unterminated_inputs_report_issues_without_raising() -> None:
    string_lexer = Lexer("'open string")
    comment_lexer = Lexer("/* open comment")
    tokens = string_lexer.tokenize()

    assert tokens[-1].kind is TokenKind.EOF
    assert string_lexer.issues[0].code == "lexer.unterminated_string"
    assert comment_lexer.tokenize()[-1].kind is TokenKind.EOF
    assert comment_lexer.issues[0].code == "lexer.unterminated_comment"


def test_configurable_parameter_styles() -> None:
    lexer = Lexer(":named ? $4 @ignored", LexerConfig(param_styles=frozenset({"colon"})))
    tokens = lexer.tokenize()

    assert tokens[0].kind is TokenKind.PARAM
    assert tokens[1].kind is TokenKind.OPERATOR
    assert tokens[2].kind is TokenKind.OPERATOR
    assert tokens[3].kind is TokenKind.NUMBER


def test_integer_range_operator_is_not_absorbed_into_decimal_tokens() -> None:
    tokens = Lexer("1..3").tokenize()
    assert [(token.kind, token.text) for token in tokens[:-1]] == [
        (TokenKind.NUMBER, "1"),
        (TokenKind.OPERATOR, ".."),
        (TokenKind.NUMBER, "3"),
    ]


def test_oracle_q_quote_supports_nonpaired_delimiters() -> None:
    lexer = Lexer("SELECT q'!literal!' AS value", LexerConfig(oracle_q_quotes=True))

    assert lexer.tokenize()[1].value == "literal"
    assert not lexer.issues
