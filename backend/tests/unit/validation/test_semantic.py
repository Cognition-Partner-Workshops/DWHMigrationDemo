from migraai.validation.checks import semantic


def test_known_tables_ctes_and_the_object_itself_are_resolved(make_context, make_object) -> None:
    context = make_context(
        name_map={
            "SRC.ACCOUNT": "prod.sales.account",
            "SRC.VIEW": "prod.sales.account_view",
        },
        external={"SRC.EXTERNAL"},
    )
    obj = make_object(
        content=(
            "WITH local_rows AS (SELECT id FROM prod.sales.account) "
            "SELECT local_rows.id FROM local_rows "
            "JOIN prod.sales.account_view AS current_view ON current_view.id = local_rows.id "
            "JOIN src.external AS external_rows ON external_rows.id = local_rows.id"
        ),
        dependencies=["SRC.ACCOUNT"],
    )

    assert semantic.validate(obj, context) == []


def test_unknown_table_is_an_error(make_context, make_object) -> None:
    obj = make_object(content="SELECT a.id FROM missing.schema.account AS a")

    findings = semantic.validate(obj, make_context())

    assert [finding.rule_id for finding in findings] == ["semantic.unknown_table"]
    assert findings[0].severity == "error"


def test_unknown_qualified_column_is_a_warning(make_context, make_object) -> None:
    context = make_context(
        name_map={"SRC.ACCOUNT": "prod.sales.account"},
        table_columns={"prod.sales.account": [("id", "BIGINT")]},
    )
    obj = make_object(content="SELECT a.unknown FROM prod.sales.account AS a")

    findings = semantic.validate(obj, context)

    assert [finding.rule_id for finding in findings] == ["semantic.unknown_column"]
    assert findings[0].severity == "warning"


def test_table_column_type_mismatch_and_missing_column(make_context, make_object) -> None:
    context = make_context(
        name_map={"SRC.ACCOUNT": "prod.sales.account"},
        table_columns={"prod.sales.account": [("id", "VARCHAR(20)")]},
        type_map={"INTEGER": ["BIGINT"], "VARCHAR": ["VARCHAR"]},
    )
    obj = make_object(
        content="CREATE TABLE prod.sales.account (id VARCHAR(20))",
        object_type="table",
        source_name="SRC.ACCOUNT",
        source_column_types=[("id", "INTEGER"), ("display_name", "VARCHAR(80)")],
    )

    findings = semantic.validate(obj, context)

    assert {finding.rule_id for finding in findings} == {
        "semantic.type_mismatch",
        "semantic.missing_column",
    }
    assert {finding.severity for finding in findings} == {"warning", "error"}


def test_matching_source_type_mapping_is_compatible(make_context, make_object) -> None:
    context = make_context(
        name_map={"SRC.ACCOUNT": "prod.sales.account"},
        table_columns={"prod.sales.account": [("id", "BIGINT(19)")]},
        type_map={"INTEGER": ["BIGINT"]},
    )
    obj = make_object(
        content="CREATE TABLE prod.sales.account (id BIGINT)",
        object_type="table",
        source_name="SRC.ACCOUNT",
        source_column_types=[("id", "INTEGER")],
    )

    assert semantic.validate(obj, context) == []


def test_identical_normalized_types_are_compatible(make_context, make_object) -> None:
    context = make_context(
        target="snowflake",
        name_map={"SRC.ACCOUNT": "sales.account"},
        table_columns={"sales.account": [("id", "INTEGER")]},
    )
    obj = make_object(
        content="SELECT 1",
        object_type="table",
        source_name="SRC.ACCOUNT",
        source_column_types=[("id", "INTEGER")],
    )

    assert semantic.validate(obj, context) == []


def test_snowflake_integer_and_zero_scale_number_are_synonyms(make_context, make_object) -> None:
    context = make_context(
        target="snowflake",
        name_map={"SRC.ACCOUNT": "sales.account"},
        table_columns={"sales.account": [("id", "NUMBER(38,0)")]},
        type_map={"INTEGER": ["NUMBER(38,0)"]},
    )
    obj = make_object(
        content="CREATE TABLE sales.account (id NUMBER(38,0))",
        object_type="table",
        source_name="SRC.ACCOUNT",
        source_column_types=[("id", "INTEGER")],
    )

    assert semantic.validate(obj, context) == []


def test_snowflake_varchar_length_does_not_create_type_mismatch(make_context, make_object) -> None:
    context = make_context(
        target="snowflake",
        name_map={"SRC.ACCOUNT": "sales.account"},
        table_columns={"sales.account": [("label", "VARCHAR")]},
        type_map={"VARCHAR": ["VARCHAR"]},
    )
    obj = make_object(
        content="CREATE TABLE sales.account (label VARCHAR)",
        object_type="table",
        source_name="SRC.ACCOUNT",
        source_column_types=[("label", "VARCHAR(10)")],
    )

    assert semantic.validate(obj, context) == []


def test_snowflake_real_type_mismatch_remains_a_finding(make_context, make_object) -> None:
    context = make_context(
        target="snowflake",
        name_map={"SRC.ACCOUNT": "sales.account"},
        table_columns={"sales.account": [("label", "INTEGER")]},
        type_map={"VARCHAR": ["VARCHAR"]},
    )
    obj = make_object(
        content="CREATE TABLE sales.account (label INTEGER)",
        object_type="table",
        source_name="SRC.ACCOUNT",
        source_column_types=[("label", "VARCHAR")],
    )

    findings = semantic.validate(obj, context)

    assert [finding.rule_id for finding in findings] == ["semantic.type_mismatch"]


def test_unrecognized_type_is_compared_without_aborting(make_context, make_object) -> None:
    context = make_context(
        target="snowflake",
        name_map={"SRC.TABLE": "sales.table"},
        table_columns={"sales.table": [("payload", "GRAPHIC(n)")]},
        type_map={"GRAPHIC(n)": ["GRAPHIC(n)"]},
    )
    obj = make_object(
        content="SELECT 1",
        object_type="table",
        source_name="SRC.TABLE",
        source_column_types=[("payload", "GRAPHIC(n)")],
    )

    assert semantic.validate(obj, context) == []


def test_non_sql_files_have_no_semantic_findings(make_context, make_object) -> None:
    obj = make_object(language="python", content="x = 1")

    assert semantic.validate(obj, make_context()) == []
