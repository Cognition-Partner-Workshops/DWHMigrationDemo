# Netezza SQL parser

The parser uses upper-case identifier normalization, double-quoted identifiers,
backslash client commands, dollar-number parameters, and the `::` and `..`
operators. It supports three-part names and preserves the empty middle part of
`DB..OBJ`. `SET SCHEMA` updates `default_schema`.

Shared AST nodes represent tables, views, materialized views, sequences,
synonyms, routines, principals, grants, queries, DML, and NZPLSQL blocks.
`CreateTable.options["distribution"]` uses
`DialectExpr("distribute_on", {"columns": [...]})` or `{"random": True}`;
`CreateTable.options["organize_on"]` uses
`DialectExpr("organize_on", {"columns": [...]})` or `{"none": True}`.
External-table `using` options are parsed node-valued maps, and `SAMEAS` is
stored in `CreateTable.like`.

Dialect expressions:

- `next_value` and `previous_value`: `{"sequence": QualifiedName}`.
- `raise`: `{"level": str, "format": Expr | None, "args": [Expr]}`.
- `add_user`, `drop_user`, `add_group`, and `drop_group`: `{"member": QualifiedName}`
  actions on `ALTER USER` and `ALTER GROUP`.

Dialect statements:

- `external_table_ref`: path literal, optional columns, optional `like`
  `QualifiedName`, and parsed `using` options; the path is a reads reference.
- `external_unload`: output path, `using` options, query, and an `etl_job`
  object definition.
- `groom_table`: table and typed maintenance options; writes reference.
- `generate_statistics`: optional table, columns, and `express`; reads
  reference when a table is named.
- `refresh_materialized_view`: view name; writes reference.
- `create_library` and `create_aggregate`: typed object definitions and parsed
  library location or aggregate options.

`GROOM TABLE` and `GENERATE [EXPRESS] STATISTICS` are dialect statements.
`CREATE EXTERNAL TABLE ... AS SELECT` unloads are distinct from external-table
definitions. Errors recover at statement boundaries; when an unmatched
`BEGIN_PROC` is in the failed statement, recovery skips through `END_PROC`.

Known gaps: the shared AST cannot retain labels on `While`, `Repeat`, or
`ForLoop`; sequence expressions on `DialectExpr` cannot emit reference edges
until core `RefHint` support is added. Some advanced external-table options,
NZPLSQL cursor operations, and platform-specific aggregate/library clauses
remain unsupported.
