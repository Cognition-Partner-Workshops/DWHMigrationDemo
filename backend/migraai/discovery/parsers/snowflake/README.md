# Snowflake parser

`Parser` emits the shared SQL AST and these Snowflake extensions:

- `DialectExpr("json_path", {"expr", "key"})` represents `expr:key`.
- `DialectExpr("temporal", {"mode", "kind", "value"})` represents `AT` and `BEFORE` table snapshots.
- `DialectExpr("identity", {"start", "increment", "order"})` represents identity column settings.
- `DialectExpr("stage_ref", {"name", "path", "stage_kind"})` represents a stage source in `COPY INTO`.
- `DialectStatement("create_stage"|"create_file_format"|"create_pipe"|"create_stream"|"create_task"|"create_tag"|"create_share"|"create_warehouse", args)` represents platform objects. All include `object_type`, `name`, `replace`, `if_not_exists`, `temporary`, and typed `properties`; streams also include `on_kind`/`on`; pipes include parsed `copy`; tasks may include `after`, `when`, and parsed `body`.
- `DialectStatement("create_masking_policy"|"create_row_access_policy", {"object_type", "name", "replace", "params", "returns", "body"})` represents masking and row-access policies.
- `DialectStatement("copy_into", {"direction", "target", "columns", "source", "properties"})` represents load and unload operations.
- `DialectStatement("use_warehouse"|"use_role", {"name"})`, `use_secondary_roles`, and `begin_transaction` represent session commands.
- `DialectStatement("put"|"list"|"remove", {"arguments"})` represents SnowSQL file commands.

`SnowflakeGrant` and `SnowflakeRevoke` retain the shared `grant`/`revoke` discriminators and add `grantee_kinds`. Reference hints emitted are `reads`, `writes`, and `runs_after`; task predecessor dependencies use `runs_after`.

Known gaps: semi-structured structured types beyond the shared type parser, specialized table/view ALTER actions, complete SQL scripting syntax, COPY options outside the common load/unload clauses, and additional Snowflake DDL clauses are not yet modeled.
