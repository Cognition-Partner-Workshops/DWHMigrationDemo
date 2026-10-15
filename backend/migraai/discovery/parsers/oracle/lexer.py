from migraai.discovery.lexer import LexerConfig

LEXER_CONFIG = LexerConfig(
    ident_quotes={'"': '"'},
    oracle_q_quotes=True,
    nested_comments=True,
    batch_separators=(r"^\s*/\s*$",),
    dot_command_prefixes=(),
    param_styles=frozenset({"colon", "ampersand"}),
    extra_operators=("@", "(+)"),
)


__all__ = ["LEXER_CONFIG"]
