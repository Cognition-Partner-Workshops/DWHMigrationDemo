## Idioms
- Choose DISTRIBUTION = HASH, ROUND_ROBIN or REPLICATE explicitly; ROUND_ROBIN is the default. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-table-azure-sql-data-warehouse?view=azure-sqldw-latest))
- Hash-distributed tables work well for large fact tables in a star schema. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-tables-distribute))
- Use round-robin for temporary staging tables, and replicate small tables across Compute nodes. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-tables-distribute))
- The distribution column of an existing table can't be changed, so data skew is fixed by re-creating the table. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-tables-distribute))
- Tables get a clustered columnstore index by default; consider heap for transient data and tables under 60 million rows. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-tables-index))
- Columnstore tables don't support varchar(max), nvarchar(max) or varbinary(max); use heap or a clustered index for those. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-tables-index))
- Use CTAS, a parallel operation, for transformations and table rebuilds. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-develop-ctas))

## Required replacements
- Replace geometry, geography and image with varbinary, text with varchar, ntext with nvarchar and timestamp with datetime2. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-tables-data-types))
- Implement surrogate keys with IDENTITY instead of sequences. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-tables-overview))
- IDENTITY values are not guaranteed to be contiguous, and CTAS can't use IDENTITY; create the table first and insert into it. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-tables-identity))
- Declare PRIMARY KEY as NONCLUSTERED NOT ENFORCED and UNIQUE as NOT ENFORCED. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-table-azure-sql-data-warehouse?view=azure-sqldw-latest))
- A distribution column can't have a DEFAULT constraint, and names can't exceed 128 characters. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-table-azure-sql-data-warehouse?view=azure-sqldw-latest))

## Unsupported constructs
- Foreign key and check constraints, computed columns, indexed views, sequences, sparse columns, synonyms, triggers, unique indexes and user-defined types are not supported. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-tables-overview))
- MERGE hints are not supported, MERGE ... WHEN NOT MATCHED INSERT isn't supported for tables with IDENTITY columns, and a table value constructor can't be the USING source. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/merge-transact-sql?view=azure-sqldw-latest))
- MERGE updating a distribution key column isn't supported before build 10.0.17829.0, and older builds can leave HASH-distributed targets inconsistent. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/merge-transact-sql?view=azure-sqldw-latest))
- The feature overview lists dedicated SQL pool MERGE as "Yes (preview)", so treat its status as disputed and test on the target build. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql/overview-features))

## Procedural code
- Stored procedures support a maximum of eight nesting levels, and @@NESTLEVEL isn't available. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-develop-stored-procedures))
- INSERT..EXECUTE, table-valued parameters, read-only parameters, default parameters, execution contexts, the encryption option and the return statement are not implemented. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-develop-stored-procedures))
- Replace cursors with WHILE loops or set-based operations. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-develop-loops))
- XACT_STATE() reports a failed transaction as -2, and ERROR_LINE() is not supported. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-develop-transactions))
- A single data modification transaction is limited in size per distribution. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-develop-transactions))
- THROW is the modern way to raise exceptions, and RAISERROR is also supported. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-develop-transactions))

## Security model
- Prefer Microsoft Entra authentication, which eliminates storing passwords. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-authentication))
- Grant users the least privileges necessary through role memberships and permissions. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-overview-manage-security))
- WITH GRANT OPTION lets the grantee re-grant the permission. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/grant-transact-sql?view=sql-server-ver17))
- Every user belongs to the public role and inherits its permissions, so do not grant to public. ([source](https://learn.microsoft.com/en-us/sql/relational-databases/security/authentication-access/database-level-roles?view=sql-server-ver17))
- ALTER AUTHORIZATION changes the ownership of a securable; keep it out of converted code. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/alter-authorization-transact-sql?view=sql-server-ver17))

## ETL and orchestration
- Load with the COPY statement or PolyBase external tables, the fastest and most scalable options. ([source](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/design-elt-data-loading))
- Authenticate COPY with a Shared Access Signature or managed identity rather than storage account keys. ([source](https://learn.microsoft.com/en-us/sql/t-sql/statements/copy-into-transact-sql?view=azure-sqldw-latest))
- Orchestrate ingestion and transformation with Azure Data Factory pipelines. ([source](https://learn.microsoft.com/en-us/azure/data-factory/introduction))
