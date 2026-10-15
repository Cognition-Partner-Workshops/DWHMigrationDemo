from __future__ import annotations

from collections import Counter

import yaml

from migraai.platforms.pack import (
    TARGETS_DIR,
    evaluate_rules,
    load_pack,
    pack_fixture_results,
)


def test_synapse_pack_and_fixtures() -> None:
    pack = load_pack("synapse")
    rules = [rule for rule in pack.rules if not rule.id.startswith("core.")]

    assert pack_fixture_results("synapse") == []
    assert Counter(rule.check for rule in rules) == {
        "lint": 4,
        "rule": 11,
        "security": 6,
    }
    assert {rule.kind for rule in rules} == {
        "forbid_regex",
        "require_regex",
        "forbid_function",
    }

    fixtures_dir = TARGETS_DIR / "synapse" / "fixtures"
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


def test_synapse_merge_is_only_a_low_confidence_warning() -> None:
    pack = load_pack("synapse")
    findings = evaluate_rules(
        pack,
        "-- Converted: matched order updates\n"
        "-- Reason: the source table uses a supported MERGE form\n"
        "MERGE INTO sales.orders AS target\n"
        "USING stage.orders_load AS source\n"
        "ON target.order_id = source.order_id\n"
        "WHEN MATCHED THEN UPDATE SET total_amount = source.total_amount;",
        language="sql",
        object_type=None,
    )

    assert [(finding.rule_id, finding.severity) for finding in findings] == [
        ("synapse.rule.merge_status_review", "warning")
    ]
