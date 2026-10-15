from __future__ import annotations

import json

from migraai.discovery import ast
from migraai.discovery.parsers.synapse import Parser


def _parse_one(sql: str) -> ast.Node:
    result = Parser().parse(sql)
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    return result.statements[0]


def test_synapse_table_distribution_partition_and_ctas() -> None:
    node = _parse_one(
        "CREATE TABLE dbo.fact_sales WITH (DISTRIBUTION = HASH(customer_id), "
        "CLUSTERED COLUMNSTORE INDEX ORDER (customer_id), "
        "PARTITION (sale_id RANGE RIGHT FOR VALUES (10, 20))) "
        "AS SELECT customer_id FROM dbo.stage_sales OPTION (LABEL = 'daily load')"
    )
    assert isinstance(node, ast.CreateTable)
    assert node.options["distribution"].args["type"] == "hash"
    assert node.options["distribution"].args["columns"][0].value == "customer_id"
    assert node.options["index"].args["type"] == "clustered_columnstore"
    assert node.options["partition"].args["range"] == "right"
    assert node.options["partition"].args["values"][1].value == "20"
    assert isinstance(node.as_query, ast.Query)
    assert node.as_query.body.from_[0].name.parts[-1].value == "stage_sales"
    assert node.as_query.body.hints == ["LABEL = 'daily load'"]


def test_synapse_distribution_indexes_cetas_and_materialized_view() -> None:
    round_robin = _parse_one(
        "CREATE TABLE dbo.round_robin (id INT) WITH (DISTRIBUTION = ROUND_ROBIN, HEAP)"
    )
    replicated = _parse_one(
        "CREATE TABLE dbo.replicated (id INT) WITH (DISTRIBUTION = REPLICATE, CLUSTERED INDEX (id))"
    )
    cetas = _parse_one(
        "CREATE EXTERNAL TABLE dbo.exported_sales WITH "
        "(LOCATION = '/exports/', DATA_SOURCE = lake, FILE_FORMAT = csv_format) "
        "AS SELECT sale_id FROM dbo.stage_sales"
    )
    materialized = _parse_one(
        "CREATE MATERIALIZED VIEW dbo.mv_sales WITH "
        "(DISTRIBUTION = HASH(sale_id)) AS SELECT sale_id FROM dbo.stage_sales"
    )
    assert round_robin.options["distribution"].args["type"] == "round_robin"
    assert round_robin.options["index"].args["type"] == "heap"
    assert replicated.options["distribution"].args["type"] == "replicate"
    assert replicated.options["index"].args["type"] == "clustered"
    assert cetas.external and isinstance(cetas.as_query, ast.Query)
    assert materialized.materialized


def test_synapse_external_objects_and_secret_redaction() -> None:
    source = _parse_one(
        "CREATE EXTERNAL DATA SOURCE lake WITH (TYPE = HADOOP, "
        "LOCATION = 'https://example.test/data', CREDENTIAL = ingest_credential)"
    )
    assert isinstance(source, ast.DialectStatement)
    assert source.args["object_type"] == "external_data_source"
    assert source.args["options"]["credential"].parts[-1].value == "ingest_credential"

    file_format = _parse_one(
        "CREATE EXTERNAL FILE FORMAT csv_format WITH "
        "(FORMAT_TYPE = DELIMITEDTEXT, FORMAT_OPTIONS (FIELD_TERMINATOR = ',', "
        "STRING_DELIMITER = '\"'))"
    )
    assert file_format.args["object_type"] == "external_file_format"
    assert file_format.args["options"]["format_options"]["field_terminator"].value == ","

    table = _parse_one(
        "CREATE EXTERNAL TABLE dbo.ext_sales (sale_id INT) WITH "
        "(LOCATION = '/sales/', DATA_SOURCE = lake, FILE_FORMAT = csv_format)"
    )
    assert isinstance(table, ast.CreateTable)
    assert table.external
    assert table.options["data_source"].parts[-1].value == "lake"
    assert table.options["file_format"].parts[-1].value == "csv_format"

    credential = _parse_one(
        "CREATE DATABASE SCOPED CREDENTIAL ingest_credential WITH "
        "IDENTITY = 'Managed Identity', SECRET = 'dont-store-this'"
    )
    master_key = _parse_one("CREATE MASTER KEY ENCRYPTION BY PASSWORD = 'dont-store-this'")
    for node in (credential, master_key):
        assert "dont-store-this" not in json.dumps(node.to_dict())
    assert credential.args["options"]["secret"].value == "***"
    assert master_key.args["options"]["password"].value == "***"


def test_synapse_copy_into_and_rename() -> None:
    copy = _parse_one(
        "COPY INTO dbo.stage_sales (sale_id) FROM 'https://example.test/sales.csv' "
        "WITH (FILE_TYPE = 'CSV', FIRSTROW = 2, "
        "CREDENTIAL = (IDENTITY = 'Shared Access Signature', SECRET = 'private-token'))"
    )
    assert isinstance(copy, ast.DialectStatement)
    assert copy.name == "copy_into"
    assert copy.args["target"].parts[-1].value == "stage_sales"
    assert copy.args["sources"][0].value.endswith("sales.csv")
    assert copy.args["options"]["credential"]["secret"].value == "***"
    assert "private-token" not in json.dumps(copy.to_dict())
    assert copy.references[0].ref_kind == "writes"

    rename = _parse_one("RENAME OBJECT::dbo.stage_sales TO dbo.stage_sales_old")
    assert isinstance(rename, ast.Rename)
    assert rename.object_kind == "object"
    assert rename.new_name.parts[-1].value == "stage_sales_old"


def test_synapse_workload_and_statistics_nodes() -> None:
    group = _parse_one(
        "CREATE WORKLOAD GROUP ingestion WITH "
        "(MIN_PERCENTAGE_RESOURCE = 10, CAP_PERCENTAGE_RESOURCE = 40)"
    )
    classifier = _parse_one(
        "CREATE WORKLOAD CLASSIFIER loader WITH "
        "(WORKLOAD_GROUP = 'ingestion', MEMBERNAME = 'etl_user', IMPORTANCE = HIGH)"
    )
    stats = _parse_one("CREATE STATISTICS st_sales ON dbo.Sales (sale_id, customer_id)")
    update = _parse_one("UPDATE STATISTICS dbo.Sales (sale_id) WITH FULLSCAN")
    assert group.args["object_type"] == "workload_group"
    assert group.args["options"]["min_percentage_resource"].value == "10"
    assert classifier.args["object_type"] == "workload_classifier"
    assert isinstance(stats, ast.DialectStatement)
    assert stats.args["object_type"] == "statistics"
    assert stats.args["table"].parts[-1].value == "Sales"
    assert update.name == "update_statistics"


def test_synapse_inherits_tsql_dml_and_queries() -> None:
    result = Parser().parse(
        "SELECT TOP (2) id FROM dbo.Sales OPTION (LABEL = 'small query'); "
        "INSERT INTO dbo.archive (id) SELECT id FROM dbo.Sales"
    )
    assert not result.issues, result.issues
    query, insert = result.statements
    assert query.body.top.count.value == "2"
    assert query.body.hints == ["LABEL = 'small query'"]
    assert isinstance(insert, ast.Insert)
    assert insert.target.parts[-1].value == "archive"


def test_synapse_recovery_after_broken_and_unsupported_ddl() -> None:
    result = Parser().parse(
        "CREATE TABLE dbo.Broken (id INT, CONSTRAINT broken_fk FOREIGN KEY (id) REFERENCES ); "
        "CREATE XML SCHEMA COLLECTION dbo.unsupported AS '<schema/>'; "
        "CREATE TABLE dbo.recovery_survivor (id INT)"
    )
    assert any(isinstance(node, ast.Unparsed) for node in result.statements)
    assert isinstance(result.statements[-1], ast.CreateTable)
    assert result.statements[-1].name.parts[-1].value == "recovery_survivor"
