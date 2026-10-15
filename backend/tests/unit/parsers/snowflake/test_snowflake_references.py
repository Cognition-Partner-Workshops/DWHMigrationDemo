from migraai.discovery import ast
from migraai.discovery.objects import extract as extract_objects
from migraai.discovery.parsers.snowflake import Parser
from migraai.discovery.references import extract as extract_references


def test_snowflake_reference_hints_and_nested_query_references() -> None:
    parser = Parser()
    stage = parser.parse("CREATE STAGE st FILE_FORMAT=(FORMAT_NAME=ff)", "stage.sql").statements[0]
    assert [
        (ref.name.parts[-1].value, ref.ref_kind) for ref in extract_references(stage, Parser)
    ] == [("ff", "reads")]
    task = (
        Parser()
        .parse(
            "CREATE TASK t AFTER prior_task WHEN SYSTEM$STREAM_HAS_DATA('db.sch.stream') "
            "AS INSERT INTO target SELECT id FROM source",
            "task.sql",
        )
        .statements[0]
    )
    refs = extract_references(task, Parser)
    assert {ref.ref_kind for ref in refs} >= {"schedules", "reads", "writes"}
    assert any(ref.name.parts[-1].value == "source" for ref in refs)


def test_snowflake_object_types_status_and_dynamic_identifier_issue() -> None:
    parser = Parser()
    result = parser.parse(
        "CREATE TABLE db.t (id INT); CREATE STAGE db.st; SELECT * FROM IDENTIFIER($name)",
        "inventory.sql",
    )
    objects = extract_objects(result, "inventory.sql", Parser)
    assert [(item.type, item.qualified_name) for item in objects] == [
        ("table", "db.t"),
        ("stage", "db.st"),
    ]
    assert any(issue.code == "snowflake.dynamic_identifier" for issue in result.issues)


def test_snowflake_procedure_nested_issue_spans_are_absolute() -> None:
    result = Parser().parse(
        "CREATE PROCEDURE p() LANGUAGE SQL AS $$\nBEGIN\n SELECT FROM;\nEND;\n$$;",
        "routine.sql",
    )
    routine = result.statements[0]
    assert isinstance(routine, ast.CreateRoutine)
    assert any(issue.span and issue.span.start_line == 3 for issue in result.issues)
    objects = extract_objects(result, "routine.sql", Parser)
    assert objects[0].status == "partial"


def test_snowflake_recovery_keeps_later_statements() -> None:
    result = Parser().parse("CREATE ALERT broken; CREATE TABLE good (id INT);", "recover.sql")
    assert len(result.statements) >= 2
    assert isinstance(result.statements[-1], ast.CreateTable)


def test_snowflake_scripting_and_dynamic_sql_are_typed() -> None:
    block = Parser().parse(
        "BEGIN\n LET x INT := 1;\n x := x + 1;\n RETURN x;\nEND;",
        "block.sql",
    )
    assert not block.issues, block.issues
    assert isinstance(block.statements[0], ast.Block)
    assert isinstance(block.statements[0].statements[0], ast.Declare)
    dynamic = Parser().parse("EXECUTE IMMEDIATE $$SELECT id FROM source_table$$", "dynamic.sql")
    assert not dynamic.issues, dynamic.issues
    assert isinstance(dynamic.statements[0], ast.DynamicSql)
    assert isinstance(dynamic.statements[0].parsed[0], ast.Query)
    transaction = Parser().parse("BEGIN TRANSACTION; COMMIT;", "transaction.sql")
    assert isinstance(transaction.statements[0], ast.DialectStatement)
    assert isinstance(transaction.statements[1], ast.Commit)
