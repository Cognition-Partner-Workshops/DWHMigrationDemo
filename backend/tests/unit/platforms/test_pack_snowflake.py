from __future__ import annotations

import yaml

from migraai.platforms.pack import TARGETS_DIR, evaluate_rules, load_pack, pack_fixture_results


def test_snowflake_pack_rules_and_fixtures() -> None:
    pack = load_pack("snowflake")
    fixtures = TARGETS_DIR / "snowflake" / "fixtures"
    expected = yaml.safe_load((fixtures / "expected.yaml").read_text(encoding="utf-8"))

    assert pack_fixture_results("snowflake") == []
    assert len(pack.rules) == 15
    assert {rule.check for rule in pack.rules} == {"lint", "rule", "security"}
    assert {rule.kind for rule in pack.rules} == {
        "forbid_regex",
        "require_regex",
        "forbid_function",
    }
    assert all(rule.id.startswith("snowflake.") for rule in pack.rules)
    assert len(list((fixtures / "good").glob("*.sql"))) >= 4
    assert len(list((fixtures / "bad").glob("*.sql"))) >= 6
    exercised_ids = {rule_id for rule_ids in expected.values() for rule_id in rule_ids}
    assert {rule.id for rule in pack.rules} <= exercised_ids


def test_snowflake_credential_rule_ignores_masked_placeholder_but_flags_real_secret() -> None:
    pack = load_pack("snowflake")
    masked = evaluate_rules(
        pack,
        "CREATE USER analyst PASSWORD = '***MASKED***';",
        language="sql",
        object_type="user",
        checks={"security"},
    )
    real = evaluate_rules(
        pack,
        "CREATE USER analyst PASSWORD = 'abc';",
        language="sql",
        object_type="user",
        checks={"security"},
    )

    assert "snowflake.security.no_plaintext_credentials" not in {
        finding.rule_id for finding in masked
    }
    assert "snowflake.security.no_plaintext_credentials" in {finding.rule_id for finding in real}
