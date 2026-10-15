# Oracle parser

The Oracle `Parser` extends the ANSI parser with a package-local lexer, Oracle
expression and PL/SQL support, and SQL*Plus line commands. SQL*Plus scripts are
recognized only when a command begins a new statement at the start of a line;
`CONNECT` passwords and `DEFINE` variables with credential-like names are
redacted before commands enter the AST.

| Construct | AST representation | Test |
|---|---|---|
| SQL*Plus commands and `/` terminator | `ClientCommand`; lexer `TERMINATOR` | `test_oracle_sqlplus_client_commands_and_slash_terminator` |
| Package specification and body `defined_in` edge | `CreateRoutine(package/package_body)`; `Reference("defined_in")` | `test_oracle_package_spec_and_body_defined_in_edge` |
| Procedures | `CreateRoutine(routine_kind="procedure")` | `test_oracle_procedure` |
| Functions | `CreateRoutine(routine_kind="function")` | `test_oracle_function` |
| `BEFORE` triggers | `CreateRoutine.options["timing"]` | `test_oracle_before_trigger` |
| `AFTER` triggers | `CreateRoutine.options["timing"]` | `test_oracle_after_trigger` |
| `INSTEAD OF` triggers | `CreateRoutine.options["timing"]`; `Reference("trigger_on")` | `test_oracle_instead_of_trigger` |
| `FOR EACH ROW`, `:NEW`, `:OLD` | `CreateRoutine.options["for_each_row"]`, `Param` | `test_oracle_row_trigger_new_and_old_pseudorecords` |
| Anonymous blocks | `Block` | `test_oracle_anonymous_block` |
| Cursors | `CursorDecl`, `OpenCursor`, `OracleFetch`, `CloseCursor` | `test_oracle_cursor_declaration_and_operations` |
| `FOR` loops | `ForLoop`, `Range` | `test_oracle_for_loop` |
| `WHILE` loops | `While` | `test_oracle_while_loop` |
| `EXCEPTION WHEN` | `ExceptionHandler` | `test_oracle_exception_when_handler` |
| `RAISE_APPLICATION_ERROR` | `Call` | `test_oracle_raise_application_error` |
| `EXECUTE IMMEDIATE` with literal SQL | `DynamicSql.parsed` | `test_oracle_execute_immediate_parses_literal_sql` |
| `BULK COLLECT` | `OracleSelect.bulk_collect`, `into_list` | `test_oracle_bulk_collect` |
| `FORALL` | `DialectStatement("forall")` | `test_oracle_forall` |
| `%TYPE` | `DataType` | `test_oracle_percent_type` |
| `%ROWTYPE` | `DataType` | `test_oracle_percent_rowtype` |
| `(+)` outer join | `DialectExpr("outer_join_marker")` | `test_oracle_plus_outer_join` |
| `CONNECT BY`, `START WITH`, `PRIOR` | `OracleSelect.hierarchical`, `DialectExpr("prior")` | `test_oracle_connect_by_start_with_prior` |
| `MERGE` | `OracleMerge`, `MergeClause` | `test_oracle_merge` |
| Materialized views | `CreateView(materialized=True)` | `test_oracle_materialized_view` |
| Sequences and `seq.NEXTVAL` | `CreateSequence`, `DialectExpr("sequence_value")` | `test_oracle_sequence_nextval` |
| Private synonyms | `CreateSynonym`; `Reference("synonym_for")` | `test_oracle_private_synonym_reference` |
| Public synonyms | `CreateSynonym(public=True)`; `Reference("synonym_for")` | `test_oracle_public_synonym_reference` |
| Database links and `tbl@link` references | `QualifiedName` with DB-link qualifier; `Reference("reads")` | `test_oracle_database_link_reference` |
| Q-quoted strings | `Literal` | `test_oracle_q_quoted_string` |
| `q'[...]'` strings | `Literal` | `test_oracle_q_bracket_quoted_string` |
| `DECODE` | `FunctionCall` | `test_oracle_decode_function` |
| `NVL` | `FunctionCall` | `test_oracle_nvl_function` |
| `ROWNUM` | `DialectExpr("pseudo_column")` | `test_oracle_rownum` |
| `PIVOT` | `DialectExpr("pivot")` | `test_oracle_pivot` |
| `UNPIVOT` | `DialectExpr("unpivot")` | `test_oracle_unpivot` |

Package declarations retain nested routine declarations and their signatures.
The package body's executable initialization section is represented as a
`Block`. Oracle editioning, object types, XML DB, advanced table organization,
and several rare PL/SQL collection and trigger forms remain outside this parser
version; unsupported syntax is recovered as `Unparsed`.

`OracleSelect` adds `hierarchical`, `into_list`, and `bulk_collect` fields;
`OracleFetch` adds `bulk_collect` and `limit`. Both restore the core node kinds
`select` and `fetch` for shared consumers.
