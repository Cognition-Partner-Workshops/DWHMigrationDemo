from migraai.validation.findings import CheckResult
from migraai.validation.validate import CHECK_ORDER, pass_rate, validate_object


def test_validate_object_returns_seven_results_in_contract_order(make_context, make_object) -> None:
    context = make_context(name_map={"SRC.ACCOUNT": "prod.sales.account"})
    obj = make_object(content="SELECT id FROM prod.sales.account")

    results = validate_object(obj, context)

    assert [result.check for result in results] == list(CHECK_ORDER)
    assert len(results) == 7
    assert [result.status for result in results] == [
        "passed",
        "passed",
        "passed",
        "failed",
        "not_applicable",
        "not_applicable",
        "passed",
    ]
    assert pass_rate(results) == 0.8


def test_no_applicable_checks_have_perfect_pass_rate(make_context, make_object) -> None:
    results = validate_object(make_object(files=[]), make_context())

    assert [result.status for result in results] == ["not_applicable"] * 7
    assert pass_rate(results) == 1.0


def test_pass_rate_ignores_not_applicable_results() -> None:
    results = [
        CheckResult("syntax", "passed", []),
        CheckResult("lint", "failed", []),
        CheckResult("semantic", "not_applicable", []),
    ]

    assert pass_rate(results) == 0.5
