from migraai.validation.checks import lint


def test_pack_lint_finds_select_star_in_view(make_context, make_object) -> None:
    findings = lint.validate(
        make_object(content="CREATE VIEW v AS SELECT * FROM t"),
        make_context(),
    )

    assert [finding.rule_id for finding in findings] == ["_example.lint.no_select_star"]
    assert findings[0].severity == "error"


def test_pack_lint_ignores_sql_comments(make_context, make_object) -> None:
    text = "SELECT 1 AS marker\n-- SELECT * FROM ignored"

    assert lint.validate(make_object(content=text), make_context()) == []
