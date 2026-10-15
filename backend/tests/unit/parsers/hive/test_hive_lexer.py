from migraai.discovery.lexer import TokenKind
from migraai.discovery.parsers.hive.lexer import HiveLexer


def test_hive_quotes_variables_and_comparison_operators() -> None:
    tokens = HiveLexer(
        '`select` = "double \\" quote" AND value = \'back\\\\slash\' '
        "AND x <=> ${hivevar:db} AND a & 1 ^ 2 | 4"
    ).tokenize()
    assert tokens[0].kind is TokenKind.QUOTED_IDENT
    assert any(
        token.kind is TokenKind.STRING and token.value == 'double " quote' for token in tokens
    )
    assert any(token.kind is TokenKind.STRING and token.value == "back\\slash" for token in tokens)
    assert any(token.kind is TokenKind.PARAM and token.text == "${hivevar:db}" for token in tokens)
    assert {token.text for token in tokens if token.kind is TokenKind.OPERATOR} >= {
        "<=>",
        "&",
        "^",
        "|",
    }


def test_hive_does_not_enable_dollar_quoting() -> None:
    tokens = HiveLexer("SELECT $$not a string$$").tokenize()
    assert not any(token.kind is TokenKind.DOLLAR_BODY for token in tokens)
