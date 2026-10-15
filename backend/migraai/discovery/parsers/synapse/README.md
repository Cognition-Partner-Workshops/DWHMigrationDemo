# Azure Synapse parser

`Parser` extends the SQL Server parser and keeps Synapse distribution, index,
partition, external-object, workload, and `COPY INTO` constructs in typed AST
fields.

| Construct | AST representation | Test |
|---|---|---|
| `DISTRIBUTION = HASH` | `CreateTable.options["distribution"]` | `test_synapse_hash_distribution` |
| `DISTRIBUTION = ROUND_ROBIN` | `CreateTable.options["distribution"]` | `test_synapse_round_robin_distribution` |
| `DISTRIBUTION = REPLICATE` | `CreateTable.options["distribution"]` | `test_synapse_replicate_distribution` |
| `CLUSTERED COLUMNSTORE INDEX` | `DialectExpr("table_index")` | `test_synapse_clustered_columnstore_index` |
| `HEAP` | `DialectExpr("table_index")` | `test_synapse_heap_index` |
| `PARTITION` | `DialectExpr("partition")` | `test_synapse_partition` |
| CTAS | `CreateTable.as_query` | `test_synapse_ctas` |
| External data source | `DialectStatement` object type `external_data_source` | `test_synapse_external_data_source` |
| External file format | `DialectStatement` object type `external_file_format` | `test_synapse_external_file_format` |
| External table | `CreateTable(external=True)` | `test_synapse_external_table` |
| `COPY INTO` | `DialectStatement("copy_into")` with write `RefHint` | `test_synapse_copy_into` |
| Workload group | `DialectStatement` object type `workload_group` | `test_synapse_workload_group` |
| Workload classifier | `DialectStatement` object type `workload_classifier` | `test_synapse_workload_classifier` |
| `RENAME OBJECT` | `Rename` | `test_synapse_rename_object` |
| `LABEL` option | `Select.hints` | `test_synapse_label_option` |
| Materialized views | `CreateView(materialized=True)` | `test_synapse_materialized_view` |

Statistics are modeled as catalog objects for `CREATE STATISTICS`; `UPDATE
STATISTICS` is an operation and does not define an object. External-object
metadata is kept as parsed key/value nodes, with `SECRET` and `PASSWORD` values
replaced by `***`.
