from migraai.discovery import ast
from migraai.discovery.objects import extract as extract_objects
from migraai.discovery.parsers.hive import Parser
from migraai.discovery.references import extract as extract_references
from migraai.discovery.registry import get_sql_parser


def test_hive_operator_precedence_and_rlike() -> None:
    result = Parser().parse("SELECT !a RLIKE 'x', b DIV 2 + c & 1 FROM t")
    assert not result.issues, result.issues
    projection = result.statements[0].body.projections
    assert isinstance(projection[0].expr, ast.Like)
    assert projection[0].expr.op == "rlike"
    assert isinstance(projection[0].expr.expr, ast.Unary)
    assert isinstance(projection[1].expr, ast.Binary)
    assert projection[1].expr.op == "&"


def test_hive_reference_resolution_status_and_default_schema() -> None:
    parser = Parser()
    result = parser.parse(
        "USE analytics; CREATE TABLE events (id INT); "
        "CREATE VIEW recent AS SELECT normalize(id) FROM events",
        "objects.sql",
    )
    assert not result.issues, result.issues
    assert parser.default_schema == "analytics"
    objects = extract_objects(result, "objects.sql", Parser)
    assert all(item.status == "parsed" for item in objects)
    assert [(item.type, item.qualified_name) for item in objects] == [
        ("table", "events"),
        ("view", "recent"),
    ]
    refs = extract_references(result.statements[-1], Parser)
    assert any(ref.ref_kind == "reads" and ref.name.parts[-1].value == "events" for ref in refs)
    assert any(ref.ref_kind == "calls" and ref.name.parts[-1].value == "normalize" for ref in refs)


def test_hive_registry_and_recovery() -> None:
    assert get_sql_parser("hive") is Parser
    result = Parser().parse(
        "CREATE SCHEDULED QUERY unsupported; CREATE TABLE good (id INT);",
        "recovery.sql",
    )
    assert len(result.statements) == 2
    assert isinstance(result.statements[-1], ast.CreateTable)
    assert any(issue.code == "parser.syntax" for issue in result.issues)


def test_hive_recovery_rejects_trailing_table_column_comma() -> None:
    result = Parser().parse("CREATE TABLE broken (id INT,); CREATE TABLE recovered (id INT);")
    assert len(result.statements) == 2
    assert isinstance(result.statements[0], ast.Unparsed)
    assert isinstance(result.statements[1], ast.CreateTable)
    assert result.statements[1].name.parts[-1].value == "recovered"


def test_hive_set_scope_and_dynamic_partition_shape() -> None:
    result = Parser().parse(
        "SET hivevar:tenant=acme; "
        "INSERT OVERWRITE TABLE db.events PARTITION (day='2025-01-01', region) SELECT * FROM src"
    )
    assert not result.issues, result.issues
    setting = result.statements[0]
    assert isinstance(setting, ast.SetStatement)
    assert setting.scope == "hivevar"
    assert setting.items[0].name.name.parts[-1].value == "tenant"
    insert = result.statements[1]
    assert isinstance(insert, ast.Insert)
    assert insert.overwrite is True
    assert insert.partition[0].value is not None
    assert insert.partition[1].value is None


def test_hive_references_for_load_and_multi_insert() -> None:
    result = Parser().parse(
        "LOAD DATA INPATH '/tmp/data' INTO TABLE loaded; "
        "FROM src INSERT OVERWRITE TABLE out_a SELECT id INSERT INTO TABLE out_b SELECT id"
    )
    assert not result.issues, result.issues
    load_refs = extract_references(result.statements[0], Parser)
    assert [(ref.ref_kind, ref.name.parts[-1].value) for ref in load_refs] == [("writes", "loaded")]
    multi_refs = extract_references(result.statements[1], Parser)
    assert {(ref.ref_kind, ref.name.parts[-1].value) for ref in multi_refs} >= {
        ("reads", "src"),
        ("writes", "out_a"),
        ("writes", "out_b"),
    }


def test_hive_maintenance_reference_hints() -> None:
    result = Parser().parse(
        "MSCK REPAIR TABLE partitions; "
        "ANALYZE TABLE facts COMPUTE STATISTICS; "
        "TRUNCATE TABLE stale_partition PARTITION (day='2025-01-01')"
    )
    assert not result.issues, result.issues
    assert [
        (ref.ref_kind, ref.name.parts[-1].value)
        for ref in extract_references(result.statements[0], Parser)
    ] == [("writes", "partitions")]
    assert [
        (ref.ref_kind, ref.name.parts[-1].value)
        for ref in extract_references(result.statements[1], Parser)
    ] == [("reads", "facts")]
    assert [
        (ref.ref_kind, ref.name.parts[-1].value)
        for ref in extract_references(result.statements[2], Parser)
    ] == [("writes", "stale_partition")]
