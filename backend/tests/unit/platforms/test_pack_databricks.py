from __future__ import annotations

import yaml

from migraai.platforms.pack import TARGETS_DIR, load_pack, pack_fixture_results


def test_databricks_pack_rules_and_fixtures() -> None:
    pack = load_pack("databricks")
    fixtures = TARGETS_DIR / "databricks" / "fixtures"
    expected = yaml.safe_load((fixtures / "expected.yaml").read_text(encoding="utf-8"))

    assert pack_fixture_results("databricks") == []
    assert len(pack.rules) == 14
    assert {rule.check for rule in pack.rules} == {"lint", "rule", "security"}
    assert {rule.kind for rule in pack.rules} == {
        "forbid_regex",
        "require_regex",
        "forbid_function",
    }
    assert all(rule.id.startswith("databricks.") for rule in pack.rules)
    assert len(list((fixtures / "good").glob("*.sql"))) >= 4
    assert len(list((fixtures / "bad").glob("*.sql"))) >= 6
    exercised_ids = {rule_id for rule_ids in expected.values() for rule_id in rule_ids}
    assert {rule.id for rule in pack.rules} <= exercised_ids
