# Azure Synapse parser

`Parser` extends the SQL Server parser and keeps Synapse distribution, index,
partition, external-object, workload, and `COPY INTO` constructs in typed AST
fields.

| Construct | AST representation | Test |
|---|---|---|
| Hash/round-robin/replicate distribution and table indexes | `CreateTable.options["distribution"]`, `DialectExpr("table_index")` | `test_synapse_table_distribution_partition_and_ctas` |
| Range partitioning | `DialectExpr("partition")` | `test_synapse_table_distribution_partition_and_ctas` |
| External data source and file format | `DialectStatement` object types `external_data_source`, `external_file_format` | `test_synapse_external_objects_and_secret_redaction` |
| External table and CETAS | `CreateTable(external=True)` | `test_synapse_external_objects_and_secret_redaction`, `test_synapse_distribution_indexes_cetas_and_materialized_view` |
| Scoped credential/master key | `DialectStatement` object types `credential`, `master_key` with redacted secrets | `test_synapse_external_objects_and_secret_redaction` |
| `COPY INTO` | `DialectStatement("copy_into")` with write `RefHint` | `test_synapse_copy_into_and_rename` |
| Workload groups/classifiers | `DialectStatement` object types `workload_group`, `workload_classifier` | `test_synapse_workload_and_statistics_nodes` |
| `RENAME OBJECT` | `Rename` | `test_synapse_copy_into_and_rename` |
| Materialized views | `CreateView(materialized=True)` | `test_synapse_distribution_indexes_cetas_and_materialized_view` |
| Create/update statistics | `DialectStatement("create_statistics"/"update_statistics")` | `test_synapse_workload_and_statistics_nodes` |
| Inherited T-SQL statements | Shared T-SQL AST nodes | `test_synapse_inherits_tsql_dml_and_query` |

Statistics are modeled as catalog objects for `CREATE STATISTICS`; `UPDATE
STATISTICS` is an operation and does not define an object. External-object
metadata is kept as parsed key/value nodes, with `SECRET` and `PASSWORD` values
replaced by `***`.
