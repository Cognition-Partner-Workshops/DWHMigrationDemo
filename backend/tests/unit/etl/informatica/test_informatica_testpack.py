"""Golden testpack inventory for the informatica ETL parser."""

from pathlib import Path

from tests.golden import test_testpacks as golden

PACK = Path(__file__).resolve().parents[5] / "testpacks" / "etl" / "informatica"


def test_informatica_testpack_inventory() -> None:
    golden.test_testpack_inventory_matches_golden(PACK)
