from hypothesis import given, settings
from hypothesis import strategies as st

from migraai.discovery.lexer import Lexer
from migraai.discovery.parsers.ansi import Parser


@settings(max_examples=60, deadline=2000)
@given(st.text(max_size=120))
def test_lexer_and_parser_never_raise_or_hang_on_arbitrary_or_truncated_text(text: str) -> None:
    lexer = Lexer(text)
    tokens = lexer.tokenize()
    assert tokens
    Parser().parse(text, "hypothesis.sql")

    for end in (0, len(text) // 2, max(0, len(text) - 1)):
        prefix = text[:end]
        assert Lexer(prefix).tokenize()
        Parser().parse(prefix, "truncated.sql")


_SQL_KEYWORDS = (
    "SELECT",
    "FROM",
    "WHERE",
    "WITH",
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "TABLE",
    "JOIN",
    "ON",
    "AND",
    "OR",
    "NOT",
    "CASE",
    "WHEN",
    "THEN",
    "END",
    "BEGIN",
    "IF",
    "ELSE",
    "GRANT",
)
_SQL_PUNCTUATION = ("(", ")", ",", ";", ".", "[", "]")
_SQL_OPERATORS = ("=", "<>", "<", ">", "+", "-", "*", "/", "||", "::")
_SQL_TOKEN = st.one_of(
    st.sampled_from(_SQL_KEYWORDS),
    st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,8}", fullmatch=True),
    st.sampled_from(_SQL_PUNCTUATION),
    st.sampled_from(_SQL_OPERATORS),
    st.integers(min_value=0, max_value=999).map(str),
    st.text(
        alphabet=st.characters(blacklist_characters="'\r\n"),
        max_size=12,
    ).map(lambda value: f"'{value}'"),
)


@settings(max_examples=100, deadline=4000)
@given(st.lists(_SQL_TOKEN, min_size=0, max_size=60).map(" ".join))
def test_parser_never_raises_on_generated_sql_token_sequences(sql: str) -> None:
    tokens = Lexer(sql).tokenize()
    assert tokens
    Parser().parse(sql, "generated-tokens.sql")
