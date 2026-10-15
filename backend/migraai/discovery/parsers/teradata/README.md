# Teradata SQL parser

`Parser` uses a double-quoted identifier lexer, BTEQ dot-command lines, question/colon/dollar-number parameters, and `case_rule = "insensitive"`. Binary `MOD` uses multiplication precedence; `**` is right-associative and binds more tightly than multiplication. Unary minus binds before `**`, following the shared expression parser.

Standard statements use shared typed AST nodes for tables and columns, views, routines, macros, DML, grants, principals, indexes, calls, intervals, and query expressions. Teradata-specific expressions include `td_attributes`, `range_n`, `case_n`, `range`, `interval_qualifier`, `sample`, `table_option`, `primary_index`, `identity`, `value_with_unit`, and `lock`.

Teradata-specific statements include `begin_transaction`, `locking`, `collect_statistics`, `create_join_index`, `create_hash_index`, and `select_into`. `select_into` is used for multiple or parameter targets; a single plain target is kept on `Select.into`. The shared PSM mixin parses declarations, handlers, cursors, control flow, signals, diagnostics, and special-form functions.

BTEQ `.LOGON` commands retain only the command and the connection/user token; trailing password, wallet, and extra tokens are discarded before lexing. `DATABASE` and `SET DATABASE` update the parser instance's `default_schema`. `CREATE USER` password values are replaced with `<redacted>`.

Known gaps: labels on `While`, `Repeat`, and `ForLoop` are not represented by the core AST; cursor names on cursor `ForLoop`s are parsed but not retained. Generalized Teradata table options, all `RANGE_N`/`CASE_N` partition variants, and complete `CREATE USER`/`DATABASE` option grammar are not implemented. ANSI `(+)` outer-join markers are unsupported.
