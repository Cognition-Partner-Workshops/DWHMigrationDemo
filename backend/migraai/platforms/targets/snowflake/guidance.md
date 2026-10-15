## Idioms
- Leave identifiers unquoted so they resolve in uppercase; double-quoted identifiers are case-sensitive and all identifiers are limited to 255 characters. ([source](https://docs.snowflake.com/en/sql-reference/identifiers-syntax))
- Reference objects with fully qualified database.schema.object names. ([source](https://docs.snowflake.com/en/sql-reference/name-resolution))
- Filter window-function results with QUALIFY. ([source](https://docs.snowflake.com/en/sql-reference/constructs/qualify))
- Store JSON and other semi-structured data in VARIANT, OBJECT or ARRAY columns. ([source](https://docs.snowflake.com/en/sql-reference/data-types-semistructured))
- Use TRANSIENT tables for staging data that does not need a Fail-safe period, and TEMPORARY tables for session-scoped data. ([source](https://docs.snowflake.com/en/user-guide/tables-temp-transient))
- Flatten semi-structured arrays and objects with FLATTEN. ([source](https://docs.snowflake.com/en/sql-reference/functions/flatten))

## Required replacements
- Replace secondary indexes on standard tables with clustering keys or the search optimization service; CREATE INDEX applies to hybrid tables. ([source](https://docs.snowflake.com/en/sql-reference/sql/create-index))
- Use clustering keys to co-locate data in large tables. ([source](https://docs.snowflake.com/en/user-guide/tables-clustering-keys))
- Use search optimization to speed up selective point-lookup queries. ([source](https://docs.snowflake.com/en/user-guide/search-optimization-service))
- Always pair TOP n with ORDER BY; without ORDER BY the result is non-deterministic. ([source](https://docs.snowflake.com/en/sql-reference/constructs/top_n))
- Generate surrogate keys with sequences created by CREATE SEQUENCE. ([source](https://docs.snowflake.com/en/sql-reference/sql/create-sequence))

## Unsupported constructs
- Do not rely on PRIMARY KEY, UNIQUE or FOREIGN KEY constraints being enforced on standard tables; NOT NULL is enforced. ([source](https://docs.snowflake.com/en/sql-reference/constraints-overview))
- Keep DDL out of explicit transactions; DDL statements implicitly commit and explicit transactions should hold only DML and queries. ([source](https://docs.snowflake.com/en/sql-reference/transactions))
- De-duplicate MERGE sources; when multiple source rows match one target row the result can be nondeterministic. ([source](https://docs.snowflake.com/en/sql-reference/sql/merge))

## Procedural code
- Port procedural logic to Snowflake Scripting blocks. ([source](https://docs.snowflake.com/en/developer-guide/snowflake-scripting/index))
- Declare EXECUTE AS OWNER, CALLER or RESTRICTED CALLER on every stored procedure. ([source](https://docs.snowflake.com/en/sql-reference/sql/create-procedure))
- Use cursors for row-by-row processing in Snowflake Scripting. ([source](https://docs.snowflake.com/en/developer-guide/snowflake-scripting/cursors))
- Handle errors with EXCEPTION blocks in Snowflake Scripting. ([source](https://docs.snowflake.com/en/developer-guide/snowflake-scripting/exceptions))
- Pass bind variables to EXECUTE IMMEDIATE with USING instead of concatenating values. ([source](https://docs.snowflake.com/en/sql-reference/sql/execute-immediate))

## Security model
- Never grant privileges to PUBLIC; it is automatically granted to every user and role. ([source](https://docs.snowflake.com/en/user-guide/security-access-control-overview))
- Treat ACCOUNTADMIN, the most powerful role, as an administrator-only role. ([source](https://docs.snowflake.com/en/user-guide/security-access-control-considerations))
- Grant specific privileges rather than ALL [PRIVILEGES]. ([source](https://docs.snowflake.com/en/sql-reference/sql/grant-privilege))
- Transfer ownership deliberately with GRANT OWNERSHIP. ([source](https://docs.snowflake.com/en/sql-reference/sql/grant-ownership))
- Use storage integrations instead of passing explicit cloud credentials. ([source](https://docs.snowflake.com/en/user-guide/data-load-s3-config-storage-integration))
- Use key-pair authentication instead of passwords for service users. ([source](https://docs.snowflake.com/en/user-guide/key-pair-auth))
- Store credentials in SECRET objects. ([source](https://docs.snowflake.com/en/sql-reference/sql/create-secret))
- Apply masking policies for column-level security. ([source](https://docs.snowflake.com/en/user-guide/security-column-ddm-intro))
- Apply row access policies for row-level security. ([source](https://docs.snowflake.com/en/user-guide/security-row-intro))

## ETL and orchestration
- Schedule converted jobs as tasks, and chain them into task graphs. ([source](https://docs.snowflake.com/en/user-guide/tasks-intro))
- Capture change data with streams for incremental processing. ([source](https://docs.snowflake.com/en/user-guide/streams-intro))
