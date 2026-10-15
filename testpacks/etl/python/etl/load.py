"""Load stage data into marts."""

import pandas as pd

from etl.common import log_start, log_end


def stage_orders(cur, rows):
    cur.executemany("INSERT INTO stg.orders (order_id, cust_id, order_ts, amount) VALUES (?, ?, ?, ?)", rows)


def stage_customers(cur, rows):
    cur.executemany("INSERT INTO stg.customers (cust_id, cust_name, region) VALUES (?, ?, ?)", rows)


def merge_orders(cur):
    cur.execute("INSERT INTO mart.orders SELECT order_id, cust_id, order_ts, amount FROM stg.orders")


def merge_customers(cur):
    cur.execute("INSERT INTO mart.customers SELECT cust_id, cust_name, region FROM stg.customers")


def rebuild_summary(cur):
    cur.execute(
        "INSERT INTO mart.cust_summary SELECT cust_id, SUM(amount) FROM mart.orders GROUP BY cust_id"
    )


def load_dims(conn):
    frame = pd.DataFrame({"d": []})
    frame.to_sql("dim_dates", conn, schema="mart")


def read_back(conn):
    return pd.read_sql_table("orders", conn, schema="mart")


def load_all(cur, conn, rows):
    log_start(cur, "load")
    stage_orders(cur, rows)
    stage_customers(cur, rows)
    merge_orders(cur)
    merge_customers(cur)
    rebuild_summary(cur)
    load_dims(conn)
    log_end(cur, "load")
