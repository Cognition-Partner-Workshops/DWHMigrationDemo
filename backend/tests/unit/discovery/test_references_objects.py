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


def test_nested_ctes_shadow_real_names_in_their_own_scopes() -> None:
    result = Parser().parse(
        "WITH dim_customers AS (SELECT id FROM outer_source) "
        "SELECT o.id FROM dim_customers o WHERE EXISTS ("
        "WITH dim_customers AS (SELECT id FROM nested_source) "
        "SELECT 1 FROM dim_customers i WHERE i.id = o.id)",
        "nested-ctes.sql",
    )
    assert result.issues == []
    refs = extract(result.statements[0], Parser)
    names = {".".join(part.value for part in reference.name.parts) for reference in refs}
    assert names == {"outer_source", "nested_source"}


def test_correlated_subqueries_and_aliases_only_reference_real_tables() -> None:
    result = Parser().parse(
        "SELECT o.id FROM outer_table o WHERE EXISTS ("
        "SELECT 1 FROM inner_table i WHERE i.id = o.id)",
        "correlated.sql",
    )
    assert result.issues == []
    refs = extract(result.statements[0], Parser)
    names = {".".join(part.value for part in reference.name.parts) for reference in refs}
    assert names == {"outer_table", "inner_table"}


def test_merge_reads_source_and_writes_target() -> None:
    result = Parser().parse(
        "MERGE INTO target_table t USING source_table s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET id = s.id "
        "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)",
        "merge.sql",
    )
    assert result.issues == []
    refs = extract(result.statements[0], Parser)
    assert ("reads", "source_table") in {
        (reference.ref_kind, reference.name.parts[-1].value) for reference in refs
    }
    assert ("writes", "target_table") in {
        (reference.ref_kind, reference.name.parts[-1].value) for reference in refs
    }


def test_insert_select_reports_source_reads_and_target_writes() -> None:
    result = Parser().parse("INSERT INTO target_table SELECT id FROM source_table", "insert.sql")
    refs = extract(result.statements[0], Parser)
    assert {(reference.ref_kind, reference.name.parts[-1].value) for reference in refs} == {
        ("reads", "source_table"),
        ("writes", "target_table"),
    }


def test_dynamic_sql_literals_are_parsed_for_references() -> None:
    result = Parser().parse(
        "EXECUTE IMMEDIATE 'SELECT id FROM dynamic_source'",
        "dynamic.sql",
    )
    refs = extract(result.statements[0], Parser)
    assert ("reads", "dynamic_source") in {
        (reference.ref_kind, reference.name.parts[-1].value) for reference in refs
    }


def test_builtin_functions_are_excluded_but_user_calls_are_retained() -> None:
    result = Parser().parse("SELECT COUNT(id), project_fn(id) FROM source_table", "calls.sql")
    refs = extract(result.statements[0], Parser)
    names = {reference.name.parts[-1].value.casefold() for reference in refs}
    assert "count" not in names
    assert "project_fn" in names


def test_grant_foreign_key_index_and_trigger_reference_kinds() -> None:
    result = Parser().parse(
        "CREATE TABLE child (id INT REFERENCES parent(id));"
        "CREATE INDEX child_ix ON child(id);"
        "GRANT SELECT ON TABLE child TO analyst;"
        "CREATE TRIGGER child_trg AFTER INSERT ON child "
        "FOR EACH ROW BEGIN SET x = 1; END",
        "reference-kinds.sql",
    )
    refs = [reference for node in result.statements for reference in extract(node, Parser)]
    assert {"grant_on", "foreign_key", "index_on", "trigger_on"} <= {
        reference.ref_kind for reference in refs
    }


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
