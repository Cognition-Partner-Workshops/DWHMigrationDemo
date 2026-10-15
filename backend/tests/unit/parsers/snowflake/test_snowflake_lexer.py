from migraai.discovery.lexer import TokenKind
from migraai.discovery.parsers.snowflake.lexer import SnowflakeLexer


def test_snowflake_parameters_stages_and_system_identifiers() -> None:
    lexer = SnowflakeLexer("SELECT $1, $v, @stage/path/a.csv, @%t, @~ FROM t")
    tokens = lexer.tokenize()
    params = [token.text for token in tokens if token.kind is TokenKind.PARAM]
    assert params == ["$1", "$v", "@stage/path/a.csv", "@%t", "@~"]
    system = [token.text for token in SnowflakeLexer("SYSTEM$STREAM_HAS_DATA('s')").tokenize()]
    assert system[0] == "SYSTEM$STREAM_HAS_DATA"


def test_snowflake_dollar_quoting_colon_and_backslash_strings() -> None:
    tokens = SnowflakeLexer("SELECT $$SELECT 1$$, '\\n', :v, x::STRING").tokenize()
    assert any(token.kind is TokenKind.DOLLAR_BODY and token.value == "SELECT 1" for token in tokens)
    assert any(token.kind is TokenKind.OPERATOR and token.text == ":" for token in tokens)
    assert any(token.kind is TokenKind.OPERATOR and token.text == "::" for token in tokens)
    assert any(token.kind is TokenKind.STRING and token.value == "n" for token in tokens)
