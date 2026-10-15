"""Shared PySpark helpers."""

from pyspark.sql import SparkSession


def read_stg(spark, name):
    return spark.table(f"stg.{name}")


def read_mart(spark, name):
    return spark.read.table(f"mart.{name}")


def write_stg(df, name):
    df.write.saveAsTable(f"stg.{name}")


def write_mart(df, name):
    df.write.insertInto(f"mart.{name}", overwrite=True)


def read_avro(spark):
    return spark.read.format("avro").load("/data/avro/in")


def read_text(spark):
    return spark.read.text("/data/text/in")


def write_json(df):
    df.write.json("/data/json/out")


def write_orc(df):
    df.write.orc("/data/orc/out")


def jdbc_direct(spark, url):
    return spark.read.jdbc(url, "src.ref_codes")


def jdbc_out(df, url):
    df.write.jdbc(url, "stg.ref_codes_out")


def save_generic(df):
    df.write.format("parquet").save("/data/generic/out")


def count_table(spark):
    return spark.sql("SELECT COUNT(*) FROM mart.orders")
