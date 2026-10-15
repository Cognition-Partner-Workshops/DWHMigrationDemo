from __future__ import annotations

import json

from migraai.discovery import ast
from migraai.discovery.lexer import TokenKind
from migraai.discovery.parsers.sqlserver import Parser, TSqlParserBase
from migraai.discovery.parsers.sqlserver.lexer import LEXER_CONFIG, TSqlLexer
from migraai.discovery.references import extract as extract_references


def _parse_one(sql: str) -> ast.Node:
    result = Parser().parse(sql)
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    assert all(node.kind != "unparsed" for root in result.statements for node in root.walk())
    return result.statements[0]


def test_sqlserver_lexer_temp_names_brackets_national_strings_and_go() -> None:
    lexer = TSqlLexer(
        "SELECT N'value' FROM [dbo].[#work];\nGO\nSELECT * FROM ##global",
        LEXER_CONFIG,
    )
    tokens = lexer.tokenize()
    assert [token.value for token in tokens if token.value.startswith("#")] == [
        "#work",
        "##global",
    ]
    assert any(token.kind is TokenKind.NATIONAL_STRING for token in tokens)
    assert any(token.kind is TokenKind.TERMINATOR and token.text == "GO" for token in tokens)


def test_sqlserver_declare_set_variables_and_table_variable() -> None:
    result = Parser().parse(
        "DECLARE @id INT = 1, @name NVARCHAR(20) = N'item'; SET @id += 2; SELECT * FROM @items i"
    )
    assert not result.issues, result.issues
    declarations, setting, query = result.statements
    assert isinstance(declarations, ast.Block)
    assert [item.name.value for item in declarations.declarations] == ["@id", "@name"]
    assert isinstance(setting, ast.SetStatement)
    assert isinstance(setting.items[0].value, ast.DialectExpr)
    assert isinstance(query, ast.Query)
    table = query.body.from_[0]
    assert isinstance(table, ast.DialectExpr)
    assert table.name == "table_variable"
    assert table.args["alias"].value == "i"


def test_sqlserver_top_output_apply_and_query_hints() -> None:
    node = _parse_one(
        "SELECT TOP (5) p.id FROM dbo.Product p "
        "CROSS APPLY dbo.GetPrice(p.id) AS price OPTION (RECOMPILE, MAXDOP 2)"
    )
    select = node.body
    assert isinstance(select, ast.Select)
    assert select.top.count.value == "5"
    assert select.hints == ["RECOMPILE", "MAXDOP 2"]
    join = select.from_[0]
    assert isinstance(join, ast.Join)
    assert join.join_type == "cross_apply"
    assert isinstance(join.right, ast.TableFunctionRef)


def test_sqlserver_dml_top_output_and_alias_resolution() -> None:
    insert = _parse_one("INSERT TOP (3) INTO dbo.Target (id) OUTPUT inserted.id VALUES (1)")
    assert isinstance(insert, ast.Insert)
    assert insert.top.count.value == "3"
    assert insert.output[0].expr.name.parts[-1].value == "id"

    update = _parse_one("UPDATE t SET id = 2 OUTPUT inserted.id FROM dbo.Target AS t WHERE id = 1")
    assert isinstance(update, ast.Update)
    assert [part.value for part in update.target.parts] == ["dbo", "Target"]
    assert update.alias.value == "t"
    assert update.output[0].expr.name.parts[0].value == "inserted"

    delete = _parse_one("DELETE t OUTPUT deleted.id FROM dbo.Target AS t WHERE id = 1")
    assert isinstance(delete, ast.Delete)
    assert [part.value for part in delete.target.parts] == ["dbo", "Target"]
    assert delete.output[0].expr.name.parts[0].value == "deleted"


def test_sqlserver_merge_actions_and_output() -> None:
    node = _parse_one(
        "MERGE TOP (10) INTO dbo.Target AS t USING dbo.Source AS s ON t.id = s.id "
        "WHEN MATCHED THEN UPDATE SET t.value = s.value "
        "WHEN NOT MATCHED BY TARGET THEN INSERT (id) VALUES (s.id) "
        "OUTPUT $action;"
    )
    assert isinstance(node, ast.Merge)
    assert node.top.count.value == "10"
    assert len(node.clauses) == 2
    assert isinstance(node.clauses[0].action, ast.Update)
    assert isinstance(node.clauses[1].action, ast.Insert)
    assert node.output[0].expr.name.parts[0].value == "$action"


def test_sqlserver_merge_requires_semicolon() -> None:
    result = Parser().parse(
        "MERGE INTO dbo.Target t USING dbo.Source s ON t.id = s.id WHEN MATCHED THEN DELETE"
    )
    assert isinstance(result.statements[0], ast.Unparsed)
    assert any("terminating semicolon" in issue.message for issue in result.issues)


def test_sqlserver_create_table_computed_identity_constraints_and_options() -> None:
    node = _parse_one(
        "CREATE TABLE dbo.Child (id INT IDENTITY(1,1) PRIMARY KEY, "
        "parent_id INT, total AS (id + parent_id) PERSISTED, "
        "CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES dbo.Parent(id)) "
        "ON [PRIMARY] WITH (DATA_COMPRESSION = PAGE)"
    )
    assert isinstance(node, ast.CreateTable)
    assert node.columns[0].identity.name == "identity"
    assert node.columns[2].generated is not None
    assert node.constraints[0].ref_table.parts[-1].value == "Parent"
    assert node.options["filegroup"].parts[0].value == "PRIMARY"
    assert node.options["data_compression"].name.parts[0].value == "PAGE"


def test_sqlserver_multistatement_table_function_with_table_variable_insert() -> None:
    function = _parse_one(
        "CREATE FUNCTION dbo.h(@a INT) RETURNS @r TABLE (id INT) AS BEGIN "
        "INSERT INTO @r SELECT id FROM dbo.t RETURN END"
    )
    assert isinstance(function, ast.CreateRoutine)
    assert function.params[0].name.value == "@a"
    assert function.options["function_form"] == "multi_statement"
    insert = function.body.statements[0]
    assert isinstance(insert, ast.Insert)
    assert insert.target is None
    assert insert.target_variable.name == "@r"
    assert [
        (reference.ref_kind, [part.value for part in reference.name.parts])
        for reference in extract_references(insert, Parser)
    ] == [("reads", ["dbo", "t"])]


def test_sqlserver_table_variable_insert_update_delete_targets() -> None:
    result = Parser().parse("INSERT INTO @r (id) VALUES (1); UPDATE @r SET id = 2; DELETE FROM @r")
    assert not result.issues, result.issues
    assert all(node.kind != "unparsed" for root in result.statements for node in root.walk())
    insert, update, delete = result.statements
    for node in (insert, update, delete):
        assert node.target is None
        assert node.target_variable.name == "@r"
        assert all(
            "@r" not in ".".join(part.value for part in reference.name.parts)
            for reference in extract_references(node, Parser)
        )


def test_sqlserver_create_user_login_and_default_schema() -> None:
    user = _parse_one("CREATE USER u FOR LOGIN l WITH DEFAULT_SCHEMA = dbo")
    assert isinstance(user, ast.CreatePrincipal)
    assert user.principal_kind == "user"
    assert user.options["login"].parts[-1].value == "l"
    assert user.options["default_schema"].parts[-1].value == "dbo"


def test_sqlserver_create_user_from_login_and_without_login() -> None:
    from_login = _parse_one("CREATE USER u FROM LOGIN l")
    without_login = _parse_one("CREATE USER u2 WITHOUT LOGIN")
    assert from_login.options["login"].parts[-1].value == "l"
    assert without_login.options["without_login"] is True


def test_sqlserver_create_role_authorization() -> None:
    role = _parse_one("CREATE ROLE r AUTHORIZATION dbo")
    assert isinstance(role, ast.CreatePrincipal)
    assert role.principal_kind == "role"
    assert role.options["authorization"].parts[-1].value == "dbo"


def test_sqlserver_routine_trigger_and_try_catch_nodes() -> None:
    procedure = _parse_one(
        "CREATE OR ALTER PROCEDURE dbo.refresh @id INT = 1 AS "
        "BEGIN TRY SELECT @id; END TRY BEGIN CATCH THROW; END CATCH"
    )
    assert isinstance(procedure, ast.CreateRoutine)
    assert procedure.options["or_alter"] is True
    assert isinstance(procedure.body, ast.Block)
    assert procedure.body.exception_handlers[0].conditions == ["catch"]

    trigger = _parse_one(
        "CREATE TRIGGER dbo.trg_orders ON dbo.Orders AFTER INSERT AS "
        "BEGIN SELECT id FROM inserted; END"
    )
    assert isinstance(trigger, ast.CreateRoutine)
    assert trigger.options["timing"] == "after"
    query = trigger.body.statements[0]
    pseudo_table = query.body.from_[0]
    assert isinstance(pseudo_table, ast.DialectExpr)
    assert pseudo_table.name == "trigger_pseudo_table"


def test_sqlserver_dynamic_sql_and_password_redaction() -> None:
    dynamic = _parse_one("EXEC sp_executesql N'SELECT id FROM dbo.Source', N'@id int', @id = 1")
    assert isinstance(dynamic, ast.DynamicSql)
    assert isinstance(dynamic.parsed[0], ast.Query)
    assert isinstance(dynamic.using[-1], ast.NamedArg)
    assert dynamic.parsed[0].body.from_[0].name.parts[-1].value == "Source"

    login = _parse_one("CREATE LOGIN loader WITH PASSWORD = 'secret-value'")
    assert isinstance(login, ast.CreatePrincipal)
    assert login.options["password"] == "***"
    assert "secret-value" not in json.dumps(login.to_dict())


def test_sqlserver_grants_linked_names_and_cursor_statements() -> None:
    grant = _parse_one("GRANT SELECT ON OBJECT::dbo.Orders TO analyst WITH GRANT OPTION")
    assert isinstance(grant, ast.Grant)
    assert grant.object_kind == "object"
    assert grant.with_grant_option

    node = _parse_one("SELECT * FROM server1.SalesDb.dbo.Orders")
    assert isinstance(node, ast.Query)
    assert len(node.body.from_[0].name.parts) == 4

    cursor = Parser().parse(
        "DECLARE order_cursor CURSOR FOR SELECT id FROM dbo.Orders; "
        "OPEN order_cursor; FETCH NEXT FROM order_cursor INTO @id; "
        "CLOSE order_cursor; DEALLOCATE order_cursor"
    )
    assert not cursor.issues, cursor.issues
    assert isinstance(cursor.statements[0], ast.CursorDecl)
    assert isinstance(cursor.statements[1], ast.OpenCursor)
    assert isinstance(cursor.statements[2], ast.Fetch)
    assert isinstance(cursor.statements[3], ast.CloseCursor)
    assert cursor.statements[4].name == "deallocate_cursor"


def test_sqlserver_recovery_continues_after_broken_and_unsupported_ddl() -> None:
    result = Parser().parse(
        "CREATE TABLE dbo.Broken (id INT, CONSTRAINT broken_fk FOREIGN KEY (id) REFERENCES ); "
        "CREATE XML SCHEMA COLLECTION dbo.Unsupported AS '<schema/>'; "
        "CREATE TABLE dbo.recovery_survivor (id INT)"
    )
    assert any(isinstance(node, ast.Unparsed) for node in result.statements)
    assert any(issue.severity == "error" for issue in result.issues)
    survivor = result.statements[-1]
    assert isinstance(survivor, ast.CreateTable)
    assert survivor.name.parts[-1].value == "recovery_survivor"


def test_sqlserver_base_parser_is_exported() -> None:
    assert issubclass(Parser, TSqlParserBase)
