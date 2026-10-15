from __future__ import annotations

from migraai.discovery import ast
from migraai.discovery.parsers.synapse import Parser


def _parse_one(sql: str) -> ast.Node:
    result = Parser().parse(sql)
    assert not result.issues, result.issues
    assert len(result.statements) == 1
    node = result.statements[0]
    assert all(child.kind != "unparsed" for child in node.walk())
    return node


def test_synapse_hash_distribution() -> None:
    table = _parse_one("CREATE TABLE dbo.hash_t (id INT) WITH (DISTRIBUTION = HASH(id))")
    assert isinstance(table, ast.CreateTable)
    assert table.options["distribution"].args["type"] == "hash"
    assert table.options["distribution"].args["columns"][0].value == "id"


def test_synapse_round_robin_distribution() -> None:
    table = _parse_one("CREATE TABLE dbo.rr_t (id INT) WITH (DISTRIBUTION = ROUND_ROBIN)")
    assert isinstance(table, ast.CreateTable)
    assert table.options["distribution"].args["type"] == "round_robin"


def test_synapse_replicate_distribution() -> None:
    table = _parse_one("CREATE TABLE dbo.rep_t (id INT) WITH (DISTRIBUTION = REPLICATE)")
    assert isinstance(table, ast.CreateTable)
    assert table.options["distribution"].args["type"] == "replicate"


def test_synapse_clustered_columnstore_index() -> None:
    table = _parse_one("CREATE TABLE dbo.cc_t (id INT) WITH (CLUSTERED COLUMNSTORE INDEX)")
    assert isinstance(table, ast.CreateTable)
    assert table.options["index"].args["type"] == "clustered_columnstore"


def test_synapse_heap_index() -> None:
    table = _parse_one("CREATE TABLE dbo.heap_t (id INT) WITH (HEAP)")
    assert isinstance(table, ast.CreateTable)
    assert table.options["index"].args["type"] == "heap"


def test_synapse_partition() -> None:
    table = _parse_one(
        "CREATE TABLE dbo.partitioned (id INT) WITH "
        "(DISTRIBUTION = HASH(id), PARTITION (id RANGE RIGHT FOR VALUES (10, 20)))"
    )
    partition = table.options["partition"]
    assert partition.name == "partition"
    assert partition.args["range"] == "right"
    assert [value.value for value in partition.args["values"]] == ["10", "20"]


def test_synapse_ctas() -> None:
    table = _parse_one(
        "CREATE TABLE dbo.ctas WITH (DISTRIBUTION = HASH(id)) AS SELECT id FROM dbo.source"
    )
    assert isinstance(table, ast.CreateTable)
    assert isinstance(table.as_query, ast.Query)
    assert table.as_query.body.from_[0].name.parts[-1].value == "source"


def test_synapse_external_data_source() -> None:
    source = _parse_one(
        "CREATE EXTERNAL DATA SOURCE lake WITH "
        "(TYPE = HADOOP, LOCATION = 'https://example.test/data')"
    )
    assert isinstance(source, ast.DialectStatement)
    assert source.args["object_type"] == "external_data_source"
    assert source.args["options"]["type"].name.parts[-1].value == "HADOOP"


def test_synapse_external_file_format() -> None:
    file_format = _parse_one(
        "CREATE EXTERNAL FILE FORMAT csv_format WITH "
        "(FORMAT_TYPE = DELIMITEDTEXT, FORMAT_OPTIONS (FIELD_TERMINATOR = ','))"
    )
    assert isinstance(file_format, ast.DialectStatement)
    assert file_format.args["object_type"] == "external_file_format"
    assert file_format.args["options"]["format_type"].name.parts[-1].value == "DELIMITEDTEXT"


def test_synapse_external_table() -> None:
    table = _parse_one(
        "CREATE EXTERNAL TABLE dbo.ext_t (id INT) WITH "
        "(LOCATION = '/data/', DATA_SOURCE = lake, FILE_FORMAT = csv_format)"
    )
    assert isinstance(table, ast.CreateTable)
    assert table.external
    assert table.options["data_source"].parts[-1].value == "lake"
    assert table.options["file_format"].parts[-1].value == "csv_format"


def test_synapse_copy_into() -> None:
    copy = _parse_one(
        "COPY INTO dbo.stage_t FROM 'https://example.test/data.csv' WITH (FILE_TYPE = 'CSV')"
    )
    assert isinstance(copy, ast.DialectStatement)
    assert copy.name == "copy_into"
    assert copy.args["target"].parts[-1].value == "stage_t"
    assert copy.references[0].ref_kind == "writes"


def test_synapse_workload_group() -> None:
    group = _parse_one(
        "CREATE WORKLOAD GROUP ingestion WITH "
        "(MIN_PERCENTAGE_RESOURCE = 10, CAP_PERCENTAGE_RESOURCE = 40)"
    )
    assert isinstance(group, ast.DialectStatement)
    assert group.args["object_type"] == "workload_group"
    assert group.args["options"]["min_percentage_resource"].value == "10"


def test_synapse_workload_classifier() -> None:
    classifier = _parse_one(
        "CREATE WORKLOAD CLASSIFIER loader WITH "
        "(WORKLOAD_GROUP = 'ingestion', MEMBERNAME = 'etl_user', IMPORTANCE = HIGH)"
    )
    assert isinstance(classifier, ast.DialectStatement)
    assert classifier.args["object_type"] == "workload_classifier"
    assert classifier.args["options"]["workload_group"].value == "ingestion"


def test_synapse_rename_object() -> None:
    rename = _parse_one("RENAME OBJECT::dbo.old_t TO dbo.new_t")
    assert isinstance(rename, ast.Rename)
    assert rename.object_kind == "object"
    assert rename.name.parts[-1].value == "old_t"
    assert rename.new_name.parts[-1].value == "new_t"


def test_synapse_label_option() -> None:
    query = _parse_one("SELECT id FROM dbo.t OPTION (LABEL = 'daily load')")
    assert isinstance(query, ast.Query)
    assert query.body.hints == ["LABEL = 'daily load'"]


def test_synapse_materialized_view() -> None:
    view = _parse_one(
        "CREATE MATERIALIZED VIEW dbo.mv WITH "
        "(DISTRIBUTION = HASH(id)) AS SELECT id FROM dbo.source"
    )
    assert isinstance(view, ast.CreateView)
    assert view.materialized is True
    assert isinstance(view.query, ast.Query)
