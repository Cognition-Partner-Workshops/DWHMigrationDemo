from __future__ import annotations

from collections import Counter

import yaml

from migraai.platforms.pack import (
    TARGETS_DIR,
    evaluate_rules,
    load_pack,
    pack_fixture_results,
)


def test_fabric_pack_and_fixtures() -> None:
    pack = load_pack("fabric")
    rules = [rule for rule in pack.rules if not rule.id.startswith("core.")]

    assert pack_fixture_results("fabric") == []
    assert Counter(rule.check for rule in rules) == {
        "lint": 5,
        "rule": 12,
        "security": 6,
    }
    assert {rule.kind for rule in rules} == {
        "forbid_regex",
        "require_regex",
        "forbid_function",
    }

    fixtures_dir = TARGETS_DIR / "fabric" / "fixtures"
    assert len(list((fixtures_dir / "good").glob("*.sql"))) >= 4
    assert len(list((fixtures_dir / "bad").glob("*.sql"))) >= 6
    assert all(
        rule.claim is not None and rule.claim.url.startswith("https://")
        for rule in rules
        if rule.check in {"rule", "security"}
    )

    expected = yaml.safe_load((fixtures_dir / "expected.yaml").read_text(encoding="utf-8"))
    expected_errors = {
        rule_id
        for rule_ids in expected.values()
        for rule_id in rule_ids
        if not rule_id.startswith("core.")
    }
    assert expected_errors == {rule.id for rule in rules if rule.severity == "error"}


def test_fabric_rejects_nvarchar_columns() -> None:
    pack = load_pack("fabric")
    findings = evaluate_rules(
        pack,
        "-- Converted: unsupported type\n"
        "-- Reason: use a supported character type\n"
        "CREATE TABLE sales.bad_types (description NVARCHAR(100));",
        language="sql",
        object_type=None,
    )

    assert "fabric.rule.no_unsupported_types" in {finding.rule_id for finding in findings}
