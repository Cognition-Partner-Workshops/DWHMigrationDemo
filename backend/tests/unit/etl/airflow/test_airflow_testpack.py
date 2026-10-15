"""Golden testpack inventory for the airflow ETL parser."""

from pathlib import Path

from tests.golden import test_testpacks as golden

PACK = Path(__file__).resolve().parents[5] / "testpacks" / "etl" / "airflow"


def test_airflow_testpack_inventory() -> None:
    golden.test_testpack_inventory_matches_golden(PACK)
