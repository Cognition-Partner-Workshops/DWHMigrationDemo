from pathlib import Path

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
