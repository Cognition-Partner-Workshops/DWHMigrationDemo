import pytest
from sqlglot.errors import ParseError

from migraai.validation.checks import security
from migraai.validation.validate import validate_object


@pytest.mark.parametrize(
    "content",
    [
        "ALTER USER demo SET password = 'secret-value'",
        "CREATE USER demo IDENTIFIED BY 'secret-value'",
        "SELECT 'AKIAABCDEFGHIJKLMNOP' AS key",
        "-----BEGIN RSA PRIVATE KEY-----",
        'api_key = "abcdefgh12345678"',
    ],
)
def test_secret_markers_are_rejected(content, make_context, make_object) -> None:
    findings = security.validate(make_object(content=content), make_context())

    assert "security.secret_in_output" in {finding.rule_id for finding in findings}
    assert all(
        finding.severity == "error"
        for finding in findings
        if finding.rule_id == "security.secret_in_output"
    )


def test_masked_secret_placeholder_is_only_a_warning_and_check_passes(
    make_context, make_object
) -> None:
    findings = security.validate(
        make_object(content="CREATE USER analyst PASSWORD = '***MASKED***'"),
        make_context(),
    )

    assert [(finding.rule_id, finding.severity) for finding in findings] == [
        ("security.masked_secret_placeholder", "warning")
    ]
    results = validate_object(
        make_object(content="CREATE USER analyst PASSWORD = '***MASKED***'"),
        make_context(),
    )
    assert next(result for result in results if result.check == "security").status == "passed"


def test_masked_logon_placeholder_is_only_a_warning_and_check_passes(
    make_context, make_object
) -> None:
    obj = make_object(content=".LOGON host/user,'***MASKED***'")
    findings = security.validate(obj, make_context())

    assert [(finding.rule_id, finding.severity) for finding in findings] == [
        ("security.masked_secret_placeholder", "warning")
    ]
    results = validate_object(obj, make_context())
    assert next(result for result in results if result.check == "security").status == "passed"


def test_real_password_remains_a_security_error(make_context, make_object) -> None:
    findings = security.validate(
        make_object(content="CREATE USER analyst PASSWORD = 'abc'"),
        make_context(),
    )

    assert ("security.secret_in_output", "error") in {
        (finding.rule_id, finding.severity) for finding in findings
    }


def test_real_logon_credential_remains_a_security_error(make_context, make_object) -> None:
    findings = security.validate(make_object(content=".LOGON host/user,realpw"), make_context())

    assert ("security.secret_in_output", "error") in {
        (finding.rule_id, finding.severity) for finding in findings
    }
    assert "security.masked_secret_placeholder" not in {finding.rule_id for finding in findings}


def test_grant_all_and_public_are_errors(make_context, make_object) -> None:
    obj = make_object(content="GRANT ALL PRIVILEGES ON TABLE prod.sales.account TO PUBLIC")

    findings = security.validate(obj, make_context())
    rule_ids = {finding.rule_id for finding in findings}

    assert "security.grant_all" in rule_ids
    assert "security.public_grant" in rule_ids
    assert "security.unverified_grant" in rule_ids


def test_privilege_widening_is_an_error(make_context, make_object) -> None:
    context = make_context(source_grants={"SRC.SCHEMA.OBJECT": {("ROLE_A", "SELECT")}})
    obj = make_object(content="GRANT INSERT ON TABLE prod.sales.account TO role_b")

    findings = security.validate(obj, context)

    assert [finding.rule_id for finding in findings] == ["security.privilege_widening"]
    assert findings[0].severity == "error"


def test_matching_source_grant_is_not_widened(make_context, make_object) -> None:
    context = make_context(source_grants={"src.schema.object": {("role_a", "select")}})
    obj = make_object(content="GRANT SELECT ON TABLE prod.sales.account TO role_a")

    assert security.validate(obj, context) == []


def test_snowflake_execute_procedure_is_equivalent_to_usage_and_role_matches(
    make_context, make_object
) -> None:
    context = make_context(
        target="snowflake",
        source_grants={"src.schema.object": {("ROLE OPERATOR'", "EXECUTE PROCEDURE")}},
    )
    obj = make_object(content="GRANT USAGE ON PROCEDURE prod.sales.account TO ROLE OPERATOR")

    assert security.validate(obj, context) == []


def test_snowflake_select_is_not_equivalent_to_source_execute(make_context, make_object) -> None:
    context = make_context(
        target="snowflake",
        source_grants={"src.schema.object": {("operator", "EXECUTE PROCEDURE")}},
    )
    obj = make_object(content="GRANT SELECT ON TABLE prod.sales.account TO operator")

    assert [finding.rule_id for finding in security.validate(obj, context)] == [
        "security.privilege_widening"
    ]


def test_grant_without_source_grants_is_a_warning(make_context, make_object) -> None:
    obj = make_object(content="GRANT SELECT ON TABLE prod.sales.account TO role_a")

    findings = security.validate(obj, make_context())

    assert [finding.rule_id for finding in findings] == ["security.unverified_grant"]
    assert findings[0].severity == "warning"


def test_grant_regex_fallback_is_used_when_sqlglot_cannot_parse(
    monkeypatch, make_context, make_object
) -> None:
    def reject_parse(*args, **kwargs):
        raise ParseError("not supported")

    monkeypatch.setattr(security.sqlglot, "parse", reject_parse)
    obj = make_object(content="GRANT SELECT ON TABLE prod.sales.account TO role_a")

    findings = security.validate(obj, make_context())

    assert [finding.rule_id for finding in findings] == ["security.unverified_grant"]
