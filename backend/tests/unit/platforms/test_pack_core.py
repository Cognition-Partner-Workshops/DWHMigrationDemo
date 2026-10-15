from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

import migraai.platforms.pack as pack_module
from migraai.platforms.pack import (
    PackError,
    Rule,
    TargetPack,
    evaluate_rules,
    load_pack,
    pack_fixture_results,
)


def _copy_example_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target_dir = tmp_path / "_example"
    shutil.copytree(pack_module.TARGETS_DIR / "_example", target_dir)
    monkeypatch.setattr(pack_module, "TARGETS_DIR", tmp_path)
    return target_dir


def _read_rules(pack_dir: Path) -> dict[str, object]:
    return yaml.safe_load((pack_dir / "rules.yaml").read_text(encoding="utf-8"))


def _write_rules(pack_dir: Path, value: dict[str, object]) -> None:
    (pack_dir / "rules.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")


def test_loads_example_pack_with_seed_claim_model() -> None:
    pack = load_pack("_example")

    assert pack.target == "_example"
    assert len(pack.rules) == 3
    assert pack.naming.target == "_example"
    assert len(pack.naming.reserved_words) >= 30
    assert pack.rules[1].claim is not None
    assert pack.rules[1].claim.url.startswith("https://docs.example.com/")


@pytest.mark.parametrize(
    ("invalid_case", "expected_error"),
    [
        ("duplicate_id", "Duplicate rule id"),
        ("wrong_prefix", "Rule id must follow"),
        ("missing_claim", "requires a claim"),
        ("explanation_rule", "must not define .explanation"),
        ("bad_headings", "guidance.md"),
        ("too_many_words", "1,500 words"),
    ],
)
def test_load_pack_rejects_invalid_packs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_case: str,
    expected_error: str,
) -> None:
    pack_dir = _copy_example_pack(tmp_path, monkeypatch)
    if invalid_case in {
        "duplicate_id",
        "wrong_prefix",
        "missing_claim",
        "explanation_rule",
    }:
        rules = _read_rules(pack_dir)
        entries = rules["rules"]
        assert isinstance(entries, list)
        if invalid_case == "duplicate_id":
            entries[1]["id"] = entries[0]["id"]
        elif invalid_case == "wrong_prefix":
            entries[0]["id"] = "other.lint.no_select_star"
        elif invalid_case == "missing_claim":
            entries[1].pop("claim")
        else:
            entries[0]["id"] = "_example.rule.explanation_required"
        _write_rules(pack_dir, rules)
    elif invalid_case == "bad_headings":
        guidance = (pack_dir / "guidance.md").read_text(encoding="utf-8")
        (pack_dir / "guidance.md").write_text(
            guidance.replace("## Required replacements", "## Idioms"),
            encoding="utf-8",
        )
    else:
        with (pack_dir / "guidance.md").open("a", encoding="utf-8") as guidance_file:
            guidance_file.write("\nword " * 1501)

    with pytest.raises(PackError, match=expected_error):
        load_pack("_example")


def test_evaluate_rules_runs_all_kinds_and_reports_first_match_lines() -> None:
    pack = load_pack("_example")
    source = (
        "-- Converted: sample violations\n"
        "-- Reason: each validator is exercised\n"
        "CREATE VIEW bad AS\n"
        "SELECT *;\n"
        "SELECT UNSAFE_UDF ();\n"
        "SELECT unsafe_udf();\n"
    )

    findings = evaluate_rules(pack, source, language="sql", object_type="view")
    by_id = {finding.rule_id: finding for finding in findings}

    assert set(by_id) == {
        "_example.lint.no_select_star",
        "_example.rule.require_from",
        "_example.security.no_unsafe_udf",
    }
    assert by_id["_example.lint.no_select_star"].line == 4
    assert by_id["_example.rule.require_from"].line is None
    assert by_id["_example.security.no_unsafe_udf"].line == 5


def test_evaluate_rules_strips_sql_comments_without_treating_quoted_dashes_as_comments() -> None:
    pack = load_pack("_example")
    source = (
        "-- Converted: safe projection\n"
        "-- Reason: source comments must not match rules\n"
        "CREATE VIEW good AS\n"
        "SELECT '-- still in a string' AS note FROM source "
        "/* SELECT * FROM hidden */; -- SELECT * FROM hidden\n"
    )

    findings = evaluate_rules(pack, source, language="sql", object_type="view")

    assert findings == []


def test_evaluate_rules_strips_python_comments_and_matches_functions_case_insensitively() -> None:
    pack = load_pack("_example")
    rule = Rule(
        id="_example.lint.no_eval",
        check="lint",
        languages=["python"],
        kind="forbid_function",
        functions=["eval"],
        severity="error",
        message="eval is not allowed",
    )
    python_pack = TargetPack(
        target=pack.target,
        guidance=pack.guidance,
        rules=[rule],
        naming=pack.naming,
    )

    findings = evaluate_rules(
        python_pack,
        "# eval('comment only')\nvalue = 1  # EVAL(source)\nresult = EVAL(source)\n",
        language="python",
        object_type=None,
        checks={"lint"},
    )

    assert len(findings) == 1
    assert findings[0].rule_id == "_example.lint.no_eval"
    assert findings[0].line == 3


@pytest.mark.parametrize(
    ("language", "valid_text", "invalid_text"),
    [
        (
            "sql",
            "-- Converted: migrated\n-- Reason: source preserved\nSELECT 1;",
            "# Converted: migrated\n# Reason: source preserved\nSELECT 1;",
        ),
        (
            "python",
            "# Converted: migrated\n# Reason: source preserved\nvalue = 1",
            "-- Converted: migrated\n-- Reason: source preserved\nvalue = 1",
        ),
    ],
)
def test_global_explanation_rule_matches_raw_language_specific_comments(
    language: str,
    valid_text: str,
    invalid_text: str,
) -> None:
    pack = load_pack("_example")
    valid_findings = evaluate_rules(
        pack,
        valid_text,
        language=language,
        object_type=None,
        checks={"rule"},
    )
    invalid_findings = evaluate_rules(
        pack,
        invalid_text,
        language=language,
        object_type=None,
        checks={"rule"},
    )

    assert all(finding.rule_id != "core.rule.explanation_block" for finding in valid_findings)
    assert any(finding.rule_id == "core.rule.explanation_block" for finding in invalid_findings)


def test_pack_fixture_results_matches_good_and_bad_goldens() -> None:
    assert pack_fixture_results("_example") == []
