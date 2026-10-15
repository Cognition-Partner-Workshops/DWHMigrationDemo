from migraai.discovery.lexer import LexerConfig
from migraai.discovery.parser_base import ParserBase


class Parser(ParserBase):
    builtin_functions = ParserBase.ansi_builtins

    def __init__(self, text: str | None = None, rel_path: str = "") -> None:
        super().__init__(
            text,
            rel_path,
            lexer_config=LexerConfig(
                ident_quotes={'"': '"', "`": "`", "[": "]"},
                oracle_q_quotes=True,
                nested_comments=True,
                dollar_quoting=True,
            ),
        )


__all__ = ["Parser"]
