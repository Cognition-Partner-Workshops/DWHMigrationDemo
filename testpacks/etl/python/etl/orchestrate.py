"""Top-level orchestration."""

from etl.common import open_conn, close_conn, report
from etl.extract import extract_all
from etl.load import load_all
from vendor import reconcile


def run_job(dsn):
    conn = open_conn(dsn)
    cur = conn.cursor()
    try:
        extract_all(cur)
        load_all(cur, conn, [])
        reconcile.finish(cur)
        conn.commit()
    finally:
        close_conn(conn)


def run_extract_only(dsn):
    conn = open_conn(dsn)
    cur = conn.cursor()
    extract_all(cur)
    close_conn(conn)


def run_report(dsn):
    conn = open_conn(dsn)
    cur = conn.cursor()
    rows = report(cur)
    close_conn(conn)
    return rows


def main():
    run_job("DSN=dwh")
    run_report("DSN=dwh")


if __name__ == "__main__":
    main()
