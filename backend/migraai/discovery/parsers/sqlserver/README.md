# SQL Server parser

The parser exposes `Parser` and `TSqlParserBase`. It uses a package-local lexer for
temporary table names and recognizes `GO` batch separators.

| Construct | AST representation | Test |
|---|---|---|
| `GO` batches | Lexer `TERMINATOR` token; parsed batch statements | `test_sqlserver_go_batches` |
| Procedures | `CreateRoutine(routine_kind="procedure")` | `test_sqlserver_procedure` |
| Scalar functions | `CreateRoutine` with scalar `returns` | `test_sqlserver_scalar_function` |
| Inline table-valued functions | `CreateRoutine(options["function_form"]="inline_table")` | `test_sqlserver_inline_table_valued_function` |
| Multi-statement table-valued functions | `CreateRoutine(options["function_form"]="multi_statement")`, `Insert.target_variable` | `test_sqlserver_multistatement_table_valued_function` |
| Triggers and `trigger_on` reference | `CreateRoutine(routine_kind="trigger")`, `Reference("trigger_on")` | `test_sqlserver_trigger` |
| Views with `SCHEMABINDING` | `CreateView.options["schemabinding"]` | `test_sqlserver_schemabinding_view` |
| `DECLARE @vars` | `Block`, `Declare`, `Variable` | `test_sqlserver_declare_variables` |
| `SET` assignment | `SetStatement` | `test_sqlserver_set_assignment` |
| `SELECT` assignment | `Assign` | `test_sqlserver_select_assignment` |
| `IF`/`ELSE` | `If` | `test_sqlserver_if_else` |
| `WHILE` | `While` | `test_sqlserver_while_loop` |
| `TRY`/`CATCH` | `Block.exception_handlers` | `test_sqlserver_try_catch` |
| `THROW` | `Raise` | `test_sqlserver_throw` |
| `RAISERROR` | `Raise` containing `FunctionCall` | `test_sqlserver_raiserror` |
| Local `#t` temp tables | `CreateTable(temporary=True)` | `test_sqlserver_local_temp_table` |
| Global `##t` temp tables | `CreateTable(temporary=True)` | `test_sqlserver_global_temp_table` |
| Table variables | `Declare`, `DialectExpr("table_variable")` | `test_sqlserver_table_variables` |
| `INSERT` table-variable targets | `TSqlInsert.target_variable`; no writes reference | `test_sqlserver_insert_table_variable_target` |
| `UPDATE` table-variable targets | `TSqlUpdate.target_variable`; no writes reference | `test_sqlserver_update_table_variable_target` |
| `DELETE` table-variable targets | `TSqlDelete.target_variable`; no writes reference | `test_sqlserver_delete_table_variable_target` |
| `TOP` | T-SQL `Top` field on query | `test_sqlserver_top` |
| `OUTPUT` | T-SQL DML `output` field | `test_sqlserver_output` |
| `MERGE` | `TSqlMerge`, `MergeClause` | `test_sqlserver_merge` |
| `CROSS APPLY` | `Join(join_type="cross_apply")` | `test_sqlserver_cross_apply` |
| `OUTER APPLY` | `Join(join_type="outer_apply")` | `test_sqlserver_outer_apply` |
| Bracket identifiers | `Identifier(quoted=True)` | `test_sqlserver_bracket_identifiers` |
| Three-part names | `QualifiedName`; `Reference("reads")` | `test_sqlserver_three_part_name_reference` |
| Four-part linked-server names | `QualifiedName`; `Reference("reads")` | `test_sqlserver_four_part_linked_server_reference` |
| `EXEC` procedure | `Call` | `test_sqlserver_exec_procedure` |
| `sp_executesql` literal SQL and inner reads | `DynamicSql.parsed`; `Reference("reads")` | `test_sqlserver_sp_executesql_parses_literal_sql_and_reads` |
| `CREATE LOGIN` | `CreatePrincipal(principal_kind="login")` | `test_sqlserver_create_login` |
| `CREATE USER` (`FOR LOGIN`) | `CreatePrincipal(principal_kind="user")` | `test_sqlserver_create_user` |
| `CREATE USER` (`FROM LOGIN`, `WITHOUT LOGIN`) | `CreatePrincipal.options` | `test_sqlserver_create_user_from_login_and_without_login` |
| `CREATE ROLE` | `CreatePrincipal(principal_kind="role")` | `test_sqlserver_create_role` |
| `GRANT` | `Grant` | `test_sqlserver_grant` |
| `USE db` default database | `Use.database` | `test_sqlserver_use_database` |
| CTEs | `Query.with_`, `With` | `test_sqlserver_cte` |
| Window functions | `FunctionCall.over`, `WindowSpec` | `test_sqlserver_window_function` |
| `CONVERT` | `Cast` | `test_sqlserver_convert` |
| `TRY_CONVERT` | `Cast` | `test_sqlserver_try_convert` |
| `IIF` | `FunctionCall` | `test_sqlserver_iif` |

Node subclasses preserve their core kinds and add:

- `TSqlInsert`, `TSqlUpdate`, and `TSqlDelete`: `top`, `output`, `output_into`.
- `TSqlMerge`: `top`, `output`, `output_into`.

The parser currently treats temporary-table names and linked-server-qualified
names as ordinary qualified names. It does not resolve them to catalog objects.
Table-variable DML targets are stored in `target_variable` with a null catalog
`target`, so they parse without producing writes references to local variables.
