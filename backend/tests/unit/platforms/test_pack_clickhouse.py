from __future__ import annotations

import yaml

from migraai.platforms.pack import TARGETS_DIR, load_pack, pack_fixture_results


def test_clickhouse_pack_rules_and_fixtures() -> None:
    pack = load_pack("clickhouse")
    fixtures = TARGETS_DIR / "clickhouse" / "fixtures"
    expected = yaml.safe_load((fixtures / "expected.yaml").read_text(encoding="utf-8"))

    assert pack_fixture_results("clickhouse") == []
    assert len(pack.rules) == 15
    assert {rule.check for rule in pack.rules} == {"lint", "rule", "security"}
    assert {rule.kind for rule in pack.rules} == {
        "forbid_regex",
        "require_regex",
        "forbid_function",
    }
    assert all(rule.id.startswith("clickhouse.") for rule in pack.rules)
    assert len(list((fixtures / "good").glob("*.sql"))) >= 4
    assert len(list((fixtures / "bad").glob("*.sql"))) >= 6
    exercised_ids = {rule_id for rule_ids in expected.values() for rule_id in rule_ids}
    assert {rule.id for rule in pack.rules} <= exercised_ids
