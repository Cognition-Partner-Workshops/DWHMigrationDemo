from datetime import date
from pathlib import Path
from shutil import copytree

from migraai.kb.validate import validate_seed_dir

FIXTURES = Path(__file__).parents[1] / "fixtures" / "kb-validator"


def test_valid_seed_fixture() -> None:
    errors, urls = validate_seed_dir(FIXTURES / "good", allow_incomplete=True)

    assert errors == []
    assert len(urls) == 5


def test_broken_seed_fixture_reports_each_source_error() -> None:
    errors, _ = validate_seed_dir(FIXTURES / "broken", allow_incomplete=True)

    assert any("seeds requires at least 5; found 4" in error for error in errors)
    assert any("seed URL must use https" in error for error in errors)
    assert any("seed host is not allowed" in error for error in errors)


def test_complete_validation_reports_missing_expected_ids() -> None:
    errors, _ = validate_seed_dir(FIXTURES / "good", allow_incomplete=False)

    assert any("sources: missing ids:" in error for error in errors)
    assert any("pairs: missing ids:" in error for error in errors)


def test_duplicate_mapping_keys_report_both_file_lines(tmp_path: Path) -> None:
    seed_dir = tmp_path / "seed"
    copytree(FIXTURES / "good", seed_dir)
    mappings_dir = seed_dir / "mappings"
    mappings_dir.mkdir()
    (mappings_dir / "teradata.yaml").write_text(
        f"""platform: teradata
types:
  - native: " DECIMAL   (p,s) "
    canonical: decimal
    notes: first
    claim:
      text: first claim
      url: https://docs.example.test/types
      accessed: {date.today().isoformat()}
      confidence: high
  - native: "decimal (p,s)"
    canonical: decimal
    notes: second
    claim:
      text: second claim
      url: https://docs.example.test/types
      accessed: {date.today().isoformat()}
      confidence: high
functions: []
""",
        encoding="utf-8",
    )

    errors, _ = validate_seed_dir(seed_dir, allow_incomplete=True)

    duplicate_errors = [error for error in errors if "duplicate mapping key" in error]
    assert duplicate_errors == [
        f"{mappings_dir / 'teradata.yaml'}:11: duplicate mapping key "
        "(teradata, type, 'decimal (p,s)'); first declared at "
        f"{mappings_dir / 'teradata.yaml'}:3 with canonical 'decimal'; "
        "duplicate canonical 'decimal'"
    ]
