"""PySpark ingest job."""

from pyspark.sql import SparkSession


def landing_files(spark):
    return spark.read.json("/data/landing/*.json")


def landing_csv(spark):
    return spark.read.csv("/data/landing/in.csv")


def landing_orc(spark):
    return spark.read.orc("/data/landing/in.orc")


def stage_orders(df):
    df.writeTo("stg.orders").create()


def stage_events(df):
    df.write.insertInto("stg.events", overwrite=True)


def stage_text(df):
    df.write.saveAsTextFile("/data/stg/text")


def query_opt(spark):
    return spark.read.format("jdbc").option(
        "query", "SELECT acct_id, balance FROM src.accounts WHERE active = 1"
    ).load()


def jdbc_write(df):
    df.write.format("jdbc").option("dbtable", "stg.batch_out").save()


def ingest_all(spark):
    df = landing_files(spark)
    stage_events(df)
    landing_csv(spark)
    landing_orc(spark)
    query_opt(spark)
    jdbc_write(df)
