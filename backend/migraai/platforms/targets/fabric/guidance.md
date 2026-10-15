## Idioms
- Creating, altering and dropping tables, and INSERT, UPDATE and DELETE, are only supported in Warehouse, not in the SQL analytics endpoint of the Lakehouse. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area))
- IDENTITY columns are BIGINT and the system manages the values; you can't specify a starting value or increment. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/generate-unique-identifiers))
- IDENTITY values are unique but not guaranteed to be sequential, and gaps can appear. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/generate-unique-identifiers))
- The default workspace collation is case-sensitive (Latin1_General_100_BIN2_UTF8). ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/tables))
- Add PRIMARY KEY, UNIQUE and FOREIGN KEY constraints with ALTER TABLE as NOT ENFORCED; PRIMARY KEY and UNIQUE also need NONCLUSTERED. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/table-constraints))
- Reference another warehouse in the same workspace with a three-part name. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/ingest-data-tsql))

## Required replacements
- Replace money and smallmoney with decimal, datetime and smalldatetime with datetime2, nchar and nvarchar with char and varchar, text and ntext with varchar, image with varbinary and tinyint with smallint. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/data-types))
- datetime2 and time precision is limited to 6 fractional-second digits. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/data-types))
- varchar(max) and varbinary(max) are currently limited to 16 MB. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/data-types))
- Store json as varchar; xml and CLR user-defined types have no equivalent. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/data-types))
- Replace sequences with BIGINT IDENTITY columns. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/tables))

## Unsupported constructs
- Triggers, materialized views, synonyms, recursive queries, SELECT - FOR XML, SET ROWCOUNT and SET TRANSACTION ISOLATION LEVEL are not supported. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area))
- Computed columns, sequences and triggers are not supported in tables. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/tables))
- Default constraints are not supported, and constraints can't be created inline in CREATE TABLE. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/table-constraints))

## Procedural code
- Snapshot isolation is enforced for transactions. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/transactions))
- Distributed transactions and named transactions are not supported. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/transactions))
- Unsupported data types can still be used for variables, parameters and outputs of functions and stored procedures. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/data-types))
- MERGE is available for upsert logic. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/merge-transact-sql?view=fabric))

## Security model
- Authenticate with Microsoft Entra ID instead of SQL usernames and passwords. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/entra-id-authentication))
- Follow the principle of least privilege for permissions and role memberships. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/security))
- CREATE USER can't be run; GRANT or DENY creates the user automatically. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/grant-transact-sql?view=sql-server-ver17))
- GRANT ALL is deprecated, and WITH GRANT OPTION lets the grantee re-grant the permission. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/grant-transact-sql?view=sql-server-ver17))
- Every user belongs to the public role and inherits its permissions, so do not grant to public. ([source](https://learn.microsoft.com/en-us/sql/relational-databases/security/authentication-access/database-level-roles?view=sql-server-ver17))
- Use granular GRANT, DENY and REVOKE on objects. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/sql-granular-permissions))
- Apply row-level security where rows must be filtered per user. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/row-level-security))
- Mask sensitive columns with dynamic data masking. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/dynamic-data-masking))

## ETL and orchestration
- Load files with COPY INTO; when authenticating with Microsoft Entra ID you don't need to specify CREDENTIAL. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/copy-into-transact-sql?view=fabric))
- Use pipelines to ingest data into the Warehouse. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/ingest-data-pipelines))
- Use CREATE TABLE AS SELECT and INSERT ... SELECT for in-warehouse transformations. ([source](https://learn.microsoft.com/en-us/fabric/data-warehouse/ingest-data-tsql))
- Orchestrate data movement with Data Factory in Fabric. ([source](https://learn.microsoft.com/en-us/fabric/data-factory/data-factory-overview))
