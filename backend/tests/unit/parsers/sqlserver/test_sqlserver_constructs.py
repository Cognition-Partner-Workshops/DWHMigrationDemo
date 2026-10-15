from __future__ import annotations

from migraai.discovery import ast
from migraai.discovery.parsers.sqlserver import Parser
from migraai.discovery.references import extract as extract_references


def _parse_one(sql: str) -> ast.Node:
    result = Parser().parse(sql)
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    node = result.statements[0]
    assert all(child.kind != "unparsed" for child in node.walk())
    return node


def _parse_many(sql: str) -> list[ast.Node]:
    result = Parser().parse(sql)
    assert not result.issues, result.issues
    assert all(child.kind != "unparsed" for root in result.statements for child in root.walk())
    return result.statements


def test_sqlserver_go_batches() -> None:
    statements = _parse_many("SELECT 1\nGO\nSELECT 2\nGO\n")
    assert len(statements) == 2
    assert all(isinstance(node, ast.Query) for node in statements)


def test_sqlserver_procedure() -> None:
    routine = _parse_one("CREATE PROCEDURE dbo.p @id INT AS BEGIN SELECT @id; END")
    assert isinstance(routine, ast.CreateRoutine)
    assert routine.routine_kind == "procedure"
    assert routine.params[0].name.value == "@id"


def test_sqlserver_scalar_function() -> None:
    routine = _parse_one("CREATE FUNCTION dbo.f(@a INT) RETURNS INT AS BEGIN RETURN @a + 1; END")
    assert isinstance(routine, ast.CreateRoutine)
    assert routine.routine_kind == "function"
    assert routine.returns.name.parts[-1].value == "INT"
    assert routine.options == {}


def test_sqlserver_inline_table_valued_function() -> None:
    routine = _parse_one(
        "CREATE FUNCTION dbo.g(@a INT) RETURNS TABLE AS RETURN (SELECT id FROM dbo.t WHERE id = @a)"
    )
    assert isinstance(routine, ast.CreateRoutine)
    assert routine.options["function_form"] == "inline_table"
    assert isinstance(routine.body, ast.Query)


def test_sqlserver_multistatement_table_valued_function() -> None:
    routine = _parse_one(
        "CREATE FUNCTION dbo.h(@a INT) RETURNS @r TABLE (id INT) AS BEGIN "
        "INSERT INTO @r SELECT id FROM dbo.t RETURN END"
    )
    assert isinstance(routine, ast.CreateRoutine)
    assert routine.params[0].name.value == "@a"
    assert routine.options["function_form"] == "multi_statement"
    insert = routine.body.statements[0]
    assert isinstance(insert, ast.Insert)
    assert insert.target is None and insert.target_variable.name == "@r"
    assert [
        (ref.ref_kind, ref.name.parts[-1].value) for ref in extract_references(insert, Parser)
    ] == [("reads", "t")]


def test_sqlserver_trigger() -> None:
    trigger = _parse_one(
        "CREATE TRIGGER dbo.trg ON dbo.t AFTER INSERT AS BEGIN SELECT id FROM inserted; END"
    )
    assert isinstance(trigger, ast.CreateRoutine)
    assert trigger.routine_kind == "trigger"
    assert trigger.options["timing"] == "after"
    refs = extract_references(trigger, Parser)
    assert any(ref.ref_kind == "trigger_on" and ref.name.parts[-1].value == "t" for ref in refs)


def test_sqlserver_schemabinding_view() -> None:
    view = _parse_one("CREATE VIEW dbo.v WITH SCHEMABINDING AS SELECT id FROM dbo.t")
    assert isinstance(view, ast.CreateView)
    assert view.options["schemabinding"] is True
    assert isinstance(view.query, ast.Query)


def test_sqlserver_declare_variables() -> None:
    declaration = _parse_one("DECLARE @id INT = 1, @name NVARCHAR(20) = N'x'")
    assert isinstance(declaration, ast.Block)
    assert [item.name.value for item in declaration.declarations] == ["@id", "@name"]


def test_sqlserver_set_assignment() -> None:
    setting = _parse_one("SET @id = 3")
    assert isinstance(setting, ast.SetStatement)
    assert isinstance(setting.items[0].value, ast.Literal)
    assert setting.items[0].value.value == "3"


def test_sqlserver_select_assignment() -> None:
    query = _parse_one("SELECT @id = id FROM dbo.t")
    assert isinstance(query, ast.Query)
    assignment = query.body.projections[0].expr
    assert isinstance(assignment, ast.Assign)
    assert assignment.target.name == "@id"


def test_sqlserver_if_else() -> None:
    statement = _parse_one("IF @id > 0 BEGIN SET @id = 1; END ELSE SET @id = 0")
    assert isinstance(statement, ast.If)
    assert statement.else_
    assert isinstance(statement.else_[0], ast.SetStatement)


def test_sqlserver_while_loop() -> None:
    loop = _parse_one("WHILE @i < 10 BEGIN SET @i += 1; END")
    assert isinstance(loop, ast.While)
    assert loop.condition is not None
    assert isinstance(loop.statements[0], ast.Block)
    assert isinstance(loop.statements[0].statements[0], ast.SetStatement)


def test_sqlserver_try_catch() -> None:
    block = _parse_one("BEGIN TRY SELECT 1; END TRY BEGIN CATCH SELECT 2; END CATCH")
    assert isinstance(block, ast.Block)
    assert block.exception_handlers[0].conditions == ["catch"]


def test_sqlserver_throw() -> None:
    statement = _parse_one("THROW 50000, 'bad', 1")
    assert isinstance(statement, ast.Raise)
    assert statement.expr.name == "throw"
    assert len(statement.expr.args["args"]) == 3


def test_sqlserver_raiserror() -> None:
    statement = _parse_one("RAISERROR('bad', 16, 1)")
    assert isinstance(statement, ast.Raise)
    assert isinstance(statement.expr, ast.FunctionCall)
    assert statement.expr.name.parts[-1].value.casefold() == "raiserror"


def test_sqlserver_local_temp_table() -> None:
    table = _parse_one("CREATE TABLE #t (id INT)")
    assert isinstance(table, ast.CreateTable)
    assert table.temporary and table.name.parts[-1].value == "#t"


def test_sqlserver_global_temp_table() -> None:
    table = _parse_one("CREATE TABLE ##t (id INT)")
    assert isinstance(table, ast.CreateTable)
    assert table.temporary and table.name.parts[-1].value == "##t"


def test_sqlserver_table_variables() -> None:
    declaration, query = _parse_many("DECLARE @t TABLE (id INT); SELECT id FROM @t")
    assert isinstance(declaration, ast.Declare)
    assert declaration.type.name == "TABLE"
    assert query.body.from_[0].name == "table_variable"


def test_sqlserver_insert_table_variable_target() -> None:
    insert = _parse_one("INSERT INTO @r (id) VALUES (1)")
    assert isinstance(insert, ast.Insert)
    assert insert.target is None and insert.target_variable.name == "@r"
    assert not any(
        ref.ref_kind == "writes" and ref.name.parts[-1].value == "@r"
        for ref in extract_references(insert, Parser)
    )


def test_sqlserver_update_table_variable_target() -> None:
    update = _parse_one("UPDATE @r SET id = 1")
    assert isinstance(update, ast.Update)
    assert update.target is None and update.target_variable.name == "@r"
    assert not any(
        ref.ref_kind == "writes" and ref.name.parts[-1].value == "@r"
        for ref in extract_references(update, Parser)
    )


def test_sqlserver_delete_table_variable_target() -> None:
    delete = _parse_one("DELETE FROM @r")
    assert isinstance(delete, ast.Delete)
    assert delete.target is None and delete.target_variable.name == "@r"
    assert not any(
        ref.ref_kind == "writes" and ref.name.parts[-1].value == "@r"
        for ref in extract_references(delete, Parser)
    )


def test_sqlserver_top() -> None:
    query = _parse_one("SELECT TOP (5) id FROM dbo.t")
    assert query.body.top.count.value == "5"


def test_sqlserver_output() -> None:
    insert = _parse_one("INSERT INTO dbo.t OUTPUT inserted.id VALUES (1)")
    assert isinstance(insert, ast.Insert)
    assert insert.output[0].expr.name.parts[-1].value == "id"


def test_sqlserver_merge() -> None:
    merge = _parse_one(
        "MERGE INTO dbo.t AS t USING dbo.s AS s ON t.id = s.id "
        "WHEN MATCHED THEN DELETE OUTPUT $action;"
    )
    assert isinstance(merge, ast.Merge)
    assert merge.clauses[0].action.kind == "delete"
    assert merge.output[0].expr.name.parts[-1].value == "$action"


def test_sqlserver_cross_apply() -> None:
    query = _parse_one("SELECT x.v FROM dbo.t CROSS APPLY dbo.fn(t.id) x")
    assert query.body.from_[0].join_type == "cross_apply"
    assert isinstance(query.body.from_[0].right, ast.TableFunctionRef)


def test_sqlserver_outer_apply() -> None:
    query = _parse_one("SELECT x.v FROM dbo.t AS t OUTER APPLY dbo.fn(t.id) AS x")
    assert query.body.from_[0].join_type == "outer_apply"
    assert isinstance(query.body.from_[0].right, ast.TableFunctionRef)


def test_sqlserver_bracket_identifiers() -> None:
    query = _parse_one("SELECT [odd name] FROM [dbo].[table name]")
    assert query.body.projections[0].expr.name.parts[-1].value == "odd name"
    assert query.body.from_[0].name.parts[-1].value == "table name"


def test_sqlserver_three_part_name_reference() -> None:
    query = _parse_one("SELECT id FROM SalesDb.dbo.Orders")
    refs = extract_references(query, Parser)
    assert [part.value for part in refs[0].name.parts] == ["SalesDb", "dbo", "Orders"]


def test_sqlserver_four_part_linked_server_reference() -> None:
    query = _parse_one("SELECT id FROM LinkedServer.SalesDb.dbo.RemoteOrders")
    reference = extract_references(query, Parser)[0]
    assert [part.value for part in reference.name.parts] == [
        "LinkedServer",
        "SalesDb",
        "dbo",
        "RemoteOrders",
    ]


def test_sqlserver_exec_procedure() -> None:
    call = _parse_one("EXEC dbo.p @id = 1")
    assert isinstance(call, ast.Call)
    assert call.name.parts[-1].value == "p"


def test_sqlserver_sp_executesql_parses_literal_sql_and_reads() -> None:
    dynamic = _parse_one("EXEC sp_executesql N'SELECT id FROM dbo.t'")
    assert isinstance(dynamic, ast.DynamicSql)
    assert isinstance(dynamic.parsed[0], ast.Query)
    assert [
        (ref.ref_kind, ref.name.parts[-1].value) for ref in extract_references(dynamic, Parser)
    ] == [("reads", "t")]


def test_sqlserver_create_login() -> None:
    login = _parse_one("CREATE LOGIN l WITH PASSWORD = 'synthetic-secret'")
    assert isinstance(login, ast.CreatePrincipal)
    assert login.principal_kind == "login"
    assert login.options["password"] == "***"


def test_sqlserver_create_user() -> None:
    user = _parse_one("CREATE USER u FOR LOGIN l WITH DEFAULT_SCHEMA = dbo")
    assert isinstance(user, ast.CreatePrincipal)
    assert user.principal_kind == "user"
    assert user.options["login"].parts[-1].value == "l"
    assert user.options["default_schema"].parts[-1].value == "dbo"


def test_sqlserver_create_role() -> None:
    role = _parse_one("CREATE ROLE r AUTHORIZATION dbo")
    assert isinstance(role, ast.CreatePrincipal)
    assert role.principal_kind == "role"
    assert role.options["authorization"].parts[-1].value == "dbo"


def test_sqlserver_grant() -> None:
    grant = _parse_one("GRANT SELECT ON OBJECT::dbo.t TO analyst")
    assert isinstance(grant, ast.Grant)
    assert grant.privileges == ["SELECT"]
    assert grant.object_name.parts[-1].value == "t"


def test_sqlserver_use_database() -> None:
    use = _parse_one("USE SalesDb")
    assert isinstance(use, ast.Use)
    assert use.database.parts[-1].value == "SalesDb"


def test_sqlserver_cte() -> None:
    query = _parse_one("WITH c AS (SELECT id FROM dbo.t) SELECT id FROM c")
    assert isinstance(query, ast.Query)
    assert query.with_.ctes[0].name.value == "c"
    assert query.body.from_[0].name.parts[-1].value == "c"


def test_sqlserver_window_function() -> None:
    query = _parse_one("SELECT ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) AS rn FROM dbo.t")
    window = query.body.projections[0].expr.over
    assert isinstance(window, ast.WindowSpec)
    assert len(window.partition_by) == 1 and len(window.order_by) == 1


def test_sqlserver_convert() -> None:
    query = _parse_one("SELECT CONVERT(VARCHAR(10), d, 120) FROM dbo.t")
    conversion = query.body.projections[0].expr
    assert isinstance(conversion, ast.Cast)
    assert conversion.style == "convert"
    assert conversion.to.name.parts[-1].value == "VARCHAR"


def test_sqlserver_try_convert() -> None:
    query = _parse_one("SELECT TRY_CONVERT(INT, s) FROM dbo.t")
    conversion = query.body.projections[0].expr
    assert isinstance(conversion, ast.Cast)
    assert conversion.style == "convert"
    assert conversion.to.name.parts[-1].value == "INT"


def test_sqlserver_iif() -> None:
    query = _parse_one("SELECT IIF(a > 1, 'y', 'n') FROM dbo.t")
    assert query.body.projections[0].expr.name.parts[-1].value.casefold() == "iif"
