## Idioms
- Quote identifiers with backticks; double-quoted text is a string literal. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/lexical))
- Reference tables as project.dataset.table paths. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/lexical))
- Remember that table and dataset names are case-sensitive by default. ([source](https://docs.cloud.google.com/bigquery/docs/tables))
- Model nested data with STRUCT and ARRAY types. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/data-types))
- Filter window-function results with QUALIFY. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/query-syntax))
- Select only the columns you need instead of SELECT *. ([source](https://docs.cloud.google.com/bigquery/docs/best-practices-performance-compute))

## Required replacements
- Replace indexes with partitioned tables and clustered tables. ([source](https://docs.cloud.google.com/bigquery/docs/partitioned-tables))
- Cluster by at most four columns. ([source](https://docs.cloud.google.com/bigquery/docs/clustered-tables))
- Add a WHERE clause to every UPDATE, and use WHERE true to update all rows. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/dml-syntax))
- Add a WHERE clause to every DELETE; it is mandatory. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/dml-syntax))
- Replace arrays of arrays with an ARRAY of STRUCT values that contain arrays. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/data-types))
- Generate surrogate keys with GENERATE_UUID(). ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/utility-functions))
- Use SAFE_CAST where a failed conversion should return NULL. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/conversion_functions))

## Unsupported constructs
- Declare PRIMARY KEY and FOREIGN KEY as NOT ENFORCED; BigQuery does not enforce key constraints. ([source](https://docs.cloud.google.com/bigquery/docs/primary-foreign-keys))
- De-duplicate MERGE sources; a target row matching more than one source row is a runtime error. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/dml-syntax))
- Keep DDL on permanent tables, datasets and functions outside multi-statement transactions; only temporary entities are allowed. ([source](https://docs.cloud.google.com/bigquery/docs/transactions))

## Procedural code
- Port procedural logic to GoogleSQL procedural language scripts. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/procedural-language))
- Create stored procedures with CREATE PROCEDURE and call them with CALL. ([source](https://docs.cloud.google.com/bigquery/docs/procedures))
- Pass values to EXECUTE IMMEDIATE with USING instead of string concatenation. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/procedural-language))
- Use BEGIN TRANSACTION ... COMMIT TRANSACTION for multi-statement transactions. ([source](https://docs.cloud.google.com/bigquery/docs/transactions))

## Security model
- Store passwords, API keys and certificates in Secret Manager. ([source](https://docs.cloud.google.com/secret-manager/docs/overview))
- Avoid basic roles; they predate IAM and grant excessive access. ([source](https://docs.cloud.google.com/bigquery/docs/access-control-basic-roles))
- Grant predefined roles on datasets or tables with GRANT ... ON SCHEMA / TABLE. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/data-control-language))
- Never grant to allUsers or allAuthenticatedUsers. ([source](https://docs.cloud.google.com/iam/docs/principals-overview))
- Use row access policies for row-level security. ([source](https://docs.cloud.google.com/bigquery/docs/row-level-security-intro))
- Use column-level data masking for sensitive columns. ([source](https://docs.cloud.google.com/bigquery/docs/column-data-masking-intro))
- Share filtered data through authorized views. ([source](https://docs.cloud.google.com/bigquery/docs/authorized-views))

## ETL and orchestration
- Schedule recurring SQL with scheduled queries. ([source](https://docs.cloud.google.com/bigquery/docs/scheduling-queries))
- Orchestrate multi-step pipelines with Managed Service for Apache Airflow (Cloud Composer). ([source](https://docs.cloud.google.com/composer/docs/composer-3/composer-overview))
- Query external databases with EXTERNAL_QUERY federated queries. ([source](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/federated_query_functions))
