## Idioms
- Create Delta tables; Delta Lake is the default format and, unless otherwise specified, all Databricks tables are Delta tables. ([source](https://docs.databricks.com/aws/en/delta/))
- Name objects with Unity Catalog three-level names (catalog.schema.table); object names cannot exceed 255 characters and Unity Catalog stores names in lowercase. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-names))
- Quote identifiers that need special characters with backticks; identifiers are case-insensitive when referenced. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-identifiers))
- Use liquid clustering (CLUSTER BY) for new tables; it replaces table partitioning and ZORDER. ([source](https://docs.databricks.com/aws/en/tables/clustering))
- Filter window-function results with QUALIFY instead of a filtered subquery. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-qualify))
- Pair LIMIT with ORDER BY so row-limited results are deterministic. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-limit))

## Required replacements
- Replace IDENTITY(seed, step) and AUTO_INCREMENT with BIGINT GENERATED { ALWAYS | BY DEFAULT } AS IDENTITY; identity columns require Delta tables and the BIGINT type. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table-using))
- Replace secondary indexes and ZORDER-style tuning with liquid clustering keys. ([source](https://docs.databricks.com/aws/en/tables/clustering))
- Replace TOP n with ORDER BY ... LIMIT n. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-select-limit))
- Replace JSON or semi-structured text columns with the VARIANT type where supported. ([source](https://docs.databricks.com/aws/en/tables/features/variant))
- Replace Hive-format (ROW FORMAT / STORED AS) tables with Delta tables. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table-hiveformat))

## Unsupported constructs
- Do not combine liquid clustering with PARTITIONED BY or ZORDER; they are incompatible. ([source](https://docs.databricks.com/aws/en/tables/clustering))
- Do not rely on PRIMARY KEY, FOREIGN KEY or UNIQUE constraints for data quality; they are informational, while NOT NULL and CHECK constraints are enforced. ([source](https://docs.databricks.com/aws/en/tables/constraints))
- Expect MERGE INTO to fail when multiple source rows match the same target row; de-duplicate the source first. ([source](https://docs.databricks.com/aws/en/sql/language-manual/delta-merge-into))

## Procedural code
- Port procedural blocks to SQL scripting compound statements (BEGIN ... END) with variables, condition handlers, loops and IF/CASE logic. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-scripting))
- Use CREATE PROCEDURE for stored procedures; procedures are created in Unity Catalog. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-procedure))
- Use CREATE FUNCTION for SQL or Python user-defined functions. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-function))

## Security model
- Store credentials, API keys and tokens as Databricks secrets instead of plain text, and read them with the secret function. ([source](https://docs.databricks.com/aws/en/security/secrets/))
- Grant privileges to groups rather than individual users, and assign powerful privileges such as ALL PRIVILEGES and MANAGE with care. ([source](https://docs.databricks.com/aws/en/data-governance/unity-catalog/best-practices))
- Remember that object owners hold all privileges on the object, including granting to others. ([source](https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-privileges/ownership))
- Avoid broad grants to `account users`, the root group containing every user in the account. ([source](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-principal))
- Use row filters and column masks for row- and column-level security. ([source](https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/))
- Govern cloud storage access through Unity Catalog storage credentials and external locations. ([source](https://docs.databricks.com/aws/en/connect/unity-catalog/cloud-storage))

## ETL and orchestration
- Orchestrate converted batch jobs as Lakeflow Jobs tasks with dependencies and control flow. ([source](https://docs.databricks.com/aws/en/jobs/))
- Use Auto Loader (the cloudFiles source) to ingest new files incrementally from cloud storage. ([source](https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/))
- Use Lakeflow Spark Declarative Pipelines for declarative batch and streaming pipelines. ([source](https://docs.databricks.com/aws/en/ldp/))
