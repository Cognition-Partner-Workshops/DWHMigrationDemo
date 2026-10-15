# Oracle parser

The Oracle `Parser` extends the ANSI parser with a package-local lexer, Oracle
expression and PL/SQL support, and SQL*Plus line commands. SQL*Plus scripts are
recognized only when a command begins a new statement at the start of a line;
`CONNECT` passwords and `DEFINE` variables with credential-like names are
redacted before commands enter the AST.

| Construct | AST representation | Test |
|---|---|---|
| Oracle q-quotes, bind variables, slash terminators, SQL*Plus commands | `Literal`, `Param`, `ClientCommand` | `test_oracle_lexer_q_quotes_comments_parameters_and_sqlplus_batches` |
| `||`, `**`, `CASE` expressions | Core `Binary` and `Case` with Oracle precedence | `test_oracle_expression_precedence_and_case` |
| Hierarchical queries, `PRIOR`, pseudocolumns, `(+)` | `OracleSelect.hierarchical`, `DialectExpr` | `test_oracle_hierarchical_query_pseudocolumns_and_outer_join_marker` |
| `NEXTVAL`/`CURRVAL`, cursor attributes | `DialectExpr("sequence_value"/"cursor_attribute")` | `test_oracle_sequence_values_and_cursor_attributes` |
| `%TYPE`/`%ROWTYPE`, declarations, table partitions | `DataType`, `Declare`, `CreateTable.options["partition_by"]` | `test_oracle_table_types_constraints_and_partitioning` |
| Procedures/functions, defaults, ELSIF, exceptions | `CreateRoutine`, `RoutineParam`, `Block`, `If` | `test_oracle_routine_declarations_control_flow_and_exception_handler` |
| `EXECUTE IMMEDIATE`, bulk fetch, `FORALL` | `DynamicSql`, `OracleFetch`, `DialectStatement("forall")` | `test_oracle_dynamic_sql_bulk_fetch_and_forall` |
| Oracle `MERGE` conditional actions | `Merge` and wrapped actions for `DELETE WHERE`/insert filters | `test_oracle_merge_preserves_matched_and_not_matched_actions` |
| `PIVOT`/`UNPIVOT` | `DialectExpr("pivot"/"unpivot")` | `test_oracle_pivot_and_unpivot_are_typed_expressions` |
| Packages, triggers, database links | `CreateRoutine`, `DialectStatement` | `test_oracle_package_trigger_and_database_link_secrets` |
| Cursor operations, labeled loops, CASE statements, transactions | `OpenCursor`, `OracleFetch`, `Loop`, `Case` | `test_oracle_plsql_controls_cursors_and_labeled_loops` |
| Unsupported-statement recovery | `Unparsed` only on recovery | `test_oracle_recovery_preserves_following_statements` |

Package declarations retain nested routine declarations and their signatures.
The package body's executable initialization section is represented as a
`Block`. Oracle editioning, object types, XML DB, advanced table organization,
and several rare PL/SQL collection and trigger forms remain outside this parser
version; unsupported syntax is recovered as `Unparsed`.

`OracleSelect` adds `hierarchical`, `into_list`, and `bulk_collect` fields;
`OracleFetch` adds `bulk_collect` and `limit`. Both restore the core node kinds
`select` and `fetch` for shared consumers.
