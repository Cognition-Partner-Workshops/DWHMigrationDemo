# SQL Server parser

The parser exposes `Parser` and `TSqlParserBase`. It uses a package-local lexer for
temporary table names and recognizes `GO` batch separators.

| Construct | AST representation | Test |
|---|---|---|
| `#temp`/`##temp`, brackets, `N'...'`, `GO` | `Identifier`, `TokenKind.NATIONAL_STRING`, `TERMINATOR` | `test_sqlserver_lexer_temp_names_brackets_national_strings_and_go` |
| Variables, `DECLARE`, table variables, `SET` | `Variable`, `Declare`, `Block`, `SetStatement`, `DialectExpr("table_variable")` | `test_sqlserver_declare_set_variables_and_table_variable` |
| `TOP`, `APPLY`, `OPTION` | `Top`, `Join`, `Select.hints` | `test_sqlserver_top_output_apply_and_query_hints` |
| DML `TOP` and `OUTPUT` | `TSqlInsert`, `TSqlUpdate`, `TSqlDelete` | `test_sqlserver_dml_top_output_and_alias_resolution` |
| `MERGE` (requires `;`) and `$action` | `TSqlMerge`, `MergeClause`, `SelectItem` | `test_sqlserver_merge_actions_and_output` |
| Table identity/computed/FK/filegroup options | `CreateTable`, `ColumnDef`, `TableConstraint` | `test_sqlserver_create_table_computed_identity_constraints_and_options` |
| Procedures, triggers, TRY/CATCH | `CreateRoutine`, `Block`, `ExceptionHandler`, `DialectExpr("trigger_pseudo_table")` | `test_sqlserver_routine_trigger_and_try_catch_nodes` |
| `sp_executesql`, password redaction | `DynamicSql`, `NamedArg`, redacted `CreatePrincipal.options` | `test_sqlserver_dynamic_sql_and_password_redaction` |
| Grants, multipart names, cursors | `Grant`, `QualifiedName`, `CursorDecl`, cursor nodes | `test_sqlserver_grants_linked_names_and_cursor_statements` |
| Syntax and unsupported-DDL recovery | recovery `Unparsed` plus subsequent `CreateTable` | `test_sqlserver_recovery_continues_after_broken_and_unsupported_ddl` |

Node subclasses preserve their core kinds and add:

- `TSqlInsert`, `TSqlUpdate`, and `TSqlDelete`: `top`, `output`, `output_into`.
- `TSqlMerge`: `top`, `output`, `output_into`.

The parser currently treats temporary-table names and linked-server-qualified
names as ordinary qualified names. It does not resolve them to catalog objects.
