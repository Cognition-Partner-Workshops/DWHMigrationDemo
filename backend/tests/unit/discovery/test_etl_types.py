from migraai.discovery import EmbeddedSql, EtlParseResult
from migraai.etl.python import common


def test_etl_result_types_are_shared_core_types() -> None:
    assert common.EmbeddedSql is EmbeddedSql
    assert common.EtlParseResult is EtlParseResult
    assert EtlParseResult().objects == []
    assert EtlParseResult().embedded_sql == []
    assert EtlParseResult().issues == []
