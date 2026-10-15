from migraai.discovery import ast
from migraai.discovery.objects import extract as extract_objects
from migraai.discovery.parsers.ansi import Parser
from migraai.discovery.references import extract


def test_references_respect_cte_scope_and_exclude_builtins() -> None:
    parser = Parser()
    result = parser.parse(
        "WITH active AS (SELECT id FROM source_table) "
        "SELECT COUNT(active.id), custom_fn(active.id) FROM active "
        "JOIN lookup_table l ON active.id = l.id",
        "refs.sql",
    )
    refs = extract(result.statements[0], Parser)
    observed = [
        (reference.ref_kind, ".".join(part.value for part in reference.name.parts))
        for reference in refs
    ]
    assert observed.count(("reads", "source_table")) == 1
    assert observed.count(("reads", "lookup_table")) == 1
    assert ("reads", "active") not in observed
    assert ("calls", "COUNT") not in observed
    assert ("calls", "custom_fn") in observed


def test_dml_ddl_grant_and_package_references() -> None:
    parser = Parser()
    statements = parser.parse(
        "CREATE TABLE child (id INT REFERENCES parent(id));"
        "CREATE INDEX ix ON parent(id);"
        "CREATE SYNONYM p FOR parent;"
        "GRANT SELECT ON TABLE parent TO analyst;"
        "INSERT INTO child SELECT id FROM parent;"
        "UPDATE child SET id = 2 FROM parent WHERE child.id = parent.id;"
        "TRUNCATE TABLE child;"
        "CREATE PACKAGE BODY sales_pkg AS END sales_pkg;"
        "CREATE TRIGGER trg AFTER INSERT ON parent FOR EACH ROW BEGIN SET x = 1; END;"
    ).statements
    refs = [reference for statement in statements for reference in extract(statement, Parser)]
    kinds = [reference.ref_kind for reference in refs]
    assert "foreign_key" in kinds
    assert "index_on" in kinds
    assert "synonym_for" in kinds
    assert "grant_on" in kinds
    assert kinds.count("writes") >= 3
    assert "defined_in" in kinds
    assert "trigger_on" in kinds


def test_object_extraction_grants_dialect_and_script_fallback() -> None:
    parser = Parser()
    result = parser.parse(
        "CREATE TABLE sales.orders (id INT);"
        "GRANT SELECT, UPDATE ON TABLE sales.orders TO analyst;"
        "COPY sales.orders FROM 'input.csv';"
    )
    objects = extract_objects(result, "grants.sql", Parser)
    assert [(item.type, item.qualified_name) for item in objects] == [
        ("table", "sales.orders"),
        ("grant", "SELECT, UPDATE on sales.orders"),
    ]

    script = extract_objects(
        parser.parse("SELECT * FROM t; UPDATE t SET id = 1;"), "only.sql", Parser
    )
    assert len(script) == 1
    assert script[0].type == "script"
    assert script[0].qualified_name == "only.sql"


def test_ast_walk_includes_every_nested_node() -> None:
    node = ast.CreateView(
        name=ast.QualifiedName(parts=[ast.Identifier(value="v")]),
        query=ast.Query(body=ast.Select(projections=[ast.SelectItem(expr=ast.Literal(value="1"))])),
    )
    assert [child.kind for child in node.walk()][:5] == [
        "create_view",
        "qualified_name",
        "identifier",
        "query",
        "select",
    ]
