from migraai.validation.checks import rule


def test_global_rule_requires_converted_and_reason_markers(make_context, make_object) -> None:
    findings = rule.validate(
        make_object(content="SELECT id FROM prod.sales.account"),
        make_context(),
    )

    assert "core.rule.explanation_block" in {finding.rule_id for finding in findings}


def test_rule_markers_are_checked_in_raw_comments(make_context, make_object) -> None:
    text = (
        "-- Converted: target syntax\n"
        "-- Reason: source syntax was unsupported\n"
        "SELECT id FROM prod.sales.account"
    )

    assert rule.validate(make_object(content=text), make_context()) == []


def test_required_regex_does_not_match_comments(make_context, make_object) -> None:
    text = "-- Converted: change\n-- Reason: why\n-- FROM source_table\nSELECT 1"

    findings = rule.validate(make_object(content=text), make_context())

    assert "_example.rule.require_from" in {finding.rule_id for finding in findings}
