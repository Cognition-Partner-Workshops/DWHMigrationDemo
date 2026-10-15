## Idioms
- Declare a table engine on every CREATE TABLE with ENGINE = engine. ([source](https://clickhouse.com/docs/reference/statements/create/table))
- Use MergeTree-family engines with an ORDER BY sorting key, or ORDER BY tuple() when no sorting is needed. ([source](https://clickhouse.com/docs/reference/engines/table-engines/mergetree-family/mergetree))
- Design the sorting key around the sparse primary index. ([source](https://clickhouse.com/docs/concepts/core-concepts/primary-indexes))
- Quote identifiers that clash with keywords with double quotes or backticks; keywords are not reserved. ([source](https://clickhouse.com/docs/reference/syntax))
- Filter window-function results with QUALIFY. ([source](https://clickhouse.com/docs/reference/statements/select/qualify))
- Use Nullable types only where NULL is needed; Nullable almost always negatively affects performance. ([source](https://clickhouse.com/docs/reference/data-types/nullable))

## Required replacements
- Replace secondary indexes with ALTER TABLE ... ADD INDEX ... TYPE data skipping indexes. ([source](https://clickhouse.com/docs/reference/statements/alter/skipping-index))
- Replace frequent UPDATE logic with ReplacingMergeTree row versions; de-duplication happens during merges. ([source](https://clickhouse.com/docs/reference/engines/table-engines/mergetree-family/replacingmergetree))
- Replace ALTER TABLE ... DELETE with lightweight DELETE FROM ... WHERE on MergeTree tables. ([source](https://clickhouse.com/docs/reference/statements/delete))
- Use lightweight UPDATE only on tables with the required block-number settings enabled. ([source](https://clickhouse.com/docs/reference/statements/update))
- Replace stored procedures with SQL user-defined functions created from lambda expressions. ([source](https://clickhouse.com/docs/reference/statements/create/function))

## Unsupported constructs
- Do not rely on multi-statement transactions; they are experimental, while single-block inserts are atomic. ([source](https://clickhouse.com/docs/concepts/features/operations/insert/transactions))
- Avoid ALTER TABLE ... UPDATE in routine loads; it is a heavy mutation not designed for frequent use. ([source](https://clickhouse.com/docs/reference/statements/alter/update))
- Use the Distributed engine for distributed query processing on multiple servers. ([source](https://clickhouse.com/docs/reference/engines/table-engines/special/distributed))

## Procedural code
- Use incremental materialized views to shift transformation cost from query time to insert time. ([source](https://clickhouse.com/docs/concepts/features/materialized-views/incremental-materialized-view))
- Express reusable calculations as SQL UDFs. ([source](https://clickhouse.com/docs/reference/statements/create/function))

## Security model
- Do not use plaintext_password; ClickHouse stores it in a SQL text file. ([source](https://clickhouse.com/docs/reference/statements/create/user))
- Grant scoped privileges and avoid WITH GRANT OPTION in converted code. ([source](https://clickhouse.com/docs/reference/statements/grant))
- Restrict the default user, which has all rights by default, and manage access through SQL-driven users and roles. ([source](https://clickhouse.com/docs/concepts/features/security/access-rights))
- Apply row policies for row-level filtering. ([source](https://clickhouse.com/docs/reference/statements/create/row-policy))
- Avoid long-term S3 keys in table definitions; use role-based extra_credentials where available. ([source](https://clickhouse.com/docs/reference/engines/table-engines/integrations/s3))

## ETL and orchestration
- Read and write object storage with the s3 table function. ([source](https://clickhouse.com/docs/reference/functions/table-functions/s3))
- Follow the ClickHouse migration overview when planning bulk data moves. ([source](https://clickhouse.com/docs/get-started/migrate/overview))
