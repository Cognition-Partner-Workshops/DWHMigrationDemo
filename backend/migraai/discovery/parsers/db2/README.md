# Db2 SQL parser

The Db2 parser keeps SQL and SQL PL constructs in the shared typed AST and
uses `DialectExpr`/`DialectStatement` for Db2-only MQTs, temporal clauses,
RCAC permissions and masks, sequence expressions, and values-into statements.
It reuses the SQL/PSM mixin for blocks, handlers, loops, signals, diagnostics,
and special-form functions.

`Db2Lexer` recognizes case-insensitive `--#SET TERMINATOR x` directives while
retaining semicolons as inner terminators. `CONNECT` CLP commands are emitted
as redacted `ClientCommand` nodes, and `SET SCHEMA` updates `default_schema`.
Passwords in `CREATE USER` are replaced with `<redacted>`.

The Db2 catalog identifies row permissions and column masks as
`row_permission` and `column_mask`. IBM's `SYSCAT.TABLES.TYPE = 'A'` identifies
aliases separately from tables and views, so Db2 aliases use a distinct
`alias` inventory object through a dialect statement; ordinary `CREATE
SYNONYM` remains the shared `synonym` object.

Known gaps include vendor-specific WLM objects, full partition-bound grammar,
and some external routine parameter styles. While/Repeat/ForLoop labels and
cursor names remain unrepresented by the shared AST.
