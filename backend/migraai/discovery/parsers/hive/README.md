# Hive SQL parser

The Hive parser uses lower-case unquoted identifiers, backtick-delimited identifiers, single- and double-quoted string
literals, Hive variable parameters, Hive type constructors, and the Hive query operators `==`, `<=>`, `DIV`, `&`, `^`,
and `|`. It supports Hive table/view/database DDL, storage and row-format clauses, Hive functions and resources,
partitioned inserts, multi-insert and FROM-first queries, lateral views, bucket sampling, Hive configuration commands,
data loading, partition maintenance, and common table alterations.

Dialect expressions:

- `bucket_sample`: `bucket`, `out_of`, and optional `on`.
- `lateral_view`: table-generating `call`, `table_alias`, `column_aliases`, and optional `"true"` `outer`.

Dialect statements:

- `add_resource`: `resource_type`, `paths`.
- `analyze`: `table`, `partition`, `for_columns`.
- `delete_resource`: `resource_type`, `path`.
- `describe`: `mode`, `object_kind`, `target`, `partition`, `column`.
- `insert_directory`: `local`, `path`, `row_format`, `stored_as`, `query`.
- `list_resources`: `resource_type`.
- `load_data`: `local`, `path`, `overwrite`, `table`, `partition`.
- `msck_repair`: `table`, optional `action`.
- `multi_insert`: shared `from` items and `inserts`.
- `reset`, `show`, `truncate_table`, and the typed table-alter actions use the fields visible in their AST `args`.

Reference hints emitted are `reads` by table references and ANALYZE, and `writes` by LOAD DATA, MSCK, TRUNCATE, and
multi-insert targets. Hive function calls not in the builtin set produce `calls` references. Constraint validation
modifiers, skew-value semantics, and partition metadata reads are retained as dialect data rather than interpreted by
discovery. STRUCT field comments use the Hive-specific `HiveStructField` subclass, preserving the shared
`struct_field` kind. Procedural Hive UDF implementations are not parsed; function class names and resource paths are
opaque literals.
