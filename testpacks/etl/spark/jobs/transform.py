"""PySpark transform job."""

from pyspark.sql import SparkSession


def get_spark():
    return SparkSession.builder.appName("etl").getOrCreate()


def read_orders(spark):
    return spark.read.table("src.orders")


def read_customers(spark):
    return spark.table("src.customers")


def read_events(spark):
    return spark.read.parquet("/data/events/in")


def read_jdbc(spark):
    return spark.read.format("jdbc").option("dbtable", "src.accounts").load()


def write_orders(df):
    df.write.saveAsTable("mart.orders")


def write_events(df):
    df.write.mode("append").parquet("/data/events/out")


def write_csv(df):
    df.write.csv("/data/export/orders")


def aggregate(spark):
    spark.sql(
        "INSERT INTO mart.daily_totals SELECT order_ts, SUM(amount) FROM src.orders GROUP BY order_ts"
    )


def delta_lookup(spark):
    from delta.tables import DeltaTable

    DeltaTable.forName(spark, "dwh.customer_dt")


def run():
    spark = get_spark()
    orders = read_orders(spark)
    write_orders(orders)
    aggregate(spark)
