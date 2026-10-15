"""Data-quality checks."""

from pyspark.sql import SparkSession


def check_nulls(spark):
    spark.sql("SELECT COUNT(*) FROM mart.orders WHERE order_id IS NULL")


def check_dupes(spark):
    spark.sql(
        "SELECT order_id, COUNT(*) FROM mart.orders GROUP BY order_id HAVING COUNT(*) > 1"
    )


def check_freshness(spark):
    spark.sql("SELECT MAX(order_ts) FROM mart.orders")


def check_referential(spark):
    spark.sql(
        "SELECT o.order_id FROM mart.orders o LEFT JOIN mart.customers c "
        "ON o.cust_id = c.cust_id WHERE c.cust_id IS NULL"
    )


def write_report(df):
    df.write.save("/data/dq/report")


def run_all(spark):
    check_nulls(spark)
    check_dupes(spark)
    check_freshness(spark)
    check_referential(spark)


def broken_fn(:
    pass


def still_ok(spark):
    spark.sql("SELECT 1")
