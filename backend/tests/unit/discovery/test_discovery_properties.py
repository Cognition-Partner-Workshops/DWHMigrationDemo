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
