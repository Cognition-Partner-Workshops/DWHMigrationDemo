from __future__ import annotations

from migraai.etl.shell.lexer import ShKind, tokenize


def kinds(text: str):
    tokens, _ = tokenize(text)
    return [(t.kind.value, t.text) for t in tokens if t.kind not in {ShKind.NEWLINE, ShKind.EOF}]


def test_operators() -> None:
    toks = kinds("a | b && c || d ; e & f >> g < h 2>&1 &> i")
    ops = [t[1] for t in toks if t[0] == "OP"]
    assert ops == ["|", "&&", "||", ";", "&", ">>", "<", "2>&1", "&>"]


def test_comments_and_continuation() -> None:
    tokens, _ = tokenize("echo a # comment\necho b \\\nc\n")
    words = [t.text for t in tokens if t.kind is ShKind.WORD]
    assert "comment" not in words
    assert "c" in words


def test_fd_redirects() -> None:
    toks = kinds("cmd 2>err.txt 1>out.txt")
    assert ("OP", "2>") in toks and ("OP", "1>") in toks


def test_quotes() -> None:
    tokens, _ = tokenize("echo 'a b' \"c $V d\"\n")
    assert tokens[1].parts == [("lit", "a b")]
    dq = tokens[2]
    assert dq.parts[0] == ("lit", "c ")
    assert dq.parts[1] == ("var", "V")


def test_brace_word_vs_op() -> None:
    toks = kinds("echo a{b,c} && { x; }")
    assert toks[1][0] == "WORD"
    assert ("OP", "{") in toks


def test_unterminated_flags() -> None:
    tokens, issues = tokenize("echo 'oops")
    assert tokens[1].unterminated is True
    assert issues and issues[0].code == "shell.syntax"


def test_heredoc_quoted_vs_expanding() -> None:
    tokens, _ = tokenize("cat <<'E'\n$x\nE\n")
    heredocs = [t for t in tokens if t.kind is ShKind.HEREDOC]
    assert heredocs[0].value == "$x"


def test_process_substitution_word() -> None:
    tokens, _ = tokenize("cat <(echo x)\n")
    # <( forms OP '<' + OP '(' — parser treats as unsupported/skip; lexer must not raise
    assert tokens
