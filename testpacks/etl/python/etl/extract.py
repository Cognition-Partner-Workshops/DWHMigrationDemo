"""Extract jobs pulling from source systems."""

from etl import common
from etl.common import log_start, log_end, fetch_all

ORDERS_Q = "SELECT order_id, cust_id, order_ts, amount FROM src.orders WHERE order_ts >= DATE '2024-01-01'"


def extract_orders(cur):
    log_start(cur, "orders")
    rows = fetch_all(cur, ORDERS_Q)
    log_end(cur, "orders")
    return rows


def extract_customers(cur):
    cur.execute("SELECT cust_id, cust_name, region FROM src.customers")
    return cur.fetchall()


def extract_products(cur):
    cur.execute("SELECT prod_id, prod_name, price FROM src.products")
    return cur.fetchall()


def extract_regions(cur):
    cur.execute("SELECT region, COUNT(*) FROM src.customers GROUP BY region")
    return cur.fetchall()


def extract_payments(cur):
    cur.executemany("SELECT pay_id, amount FROM src.payments WHERE pay_id = ?", [(1,), (2,)])
    return cur.fetchall()


def extract_audit(cur):
    cur.execute(common.LOG_SQL)
    return cur.fetchall()


def extract_all(cur):
    extract_orders(cur)
    extract_customers(cur)
    extract_products(cur)
    extract_regions(cur)
    extract_payments(cur)
    extract_audit(cur)
