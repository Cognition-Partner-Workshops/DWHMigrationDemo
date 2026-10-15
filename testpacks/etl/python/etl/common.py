"""Shared helpers for the ETL jobs."""

LOG_SQL = "SELECT job_name, status FROM ctl.job_log WHERE run_date = CURRENT_DATE"


def open_conn(dsn):
    import pyodbc

    return pyodbc.connect(dsn)


def close_conn(conn):
    conn.close()


def log_start(cur, job):
    cur.execute(
        "INSERT INTO ctl.job_log (job_name, status, started) VALUES (?, 'RUNNING', CURRENT_TIMESTAMP)",
        (job,),
    )


def log_end(cur, job):
    cur.execute(
        "UPDATE ctl.job_log SET status = 'DONE', ended = CURRENT_TIMESTAMP WHERE job_name = ?",
        (job,),
    )


def fetch_one(cur, query, params):
    cur.execute(query, params)
    return cur.fetchone()


def fetch_all(cur, query):
    cur.execute(query)
    return cur.fetchall()


def row_count(cur, table_name):
    cur.execute(f"SELECT COUNT(*) FROM ctl.{table_name}")
    return cur.fetchone()[0]


def fail(msg):
    raise RuntimeError(msg)


def retry(fn, attempts):
    for _ in range(attempts):
        try:
            return fn()
        except Exception:
            pass
    fail("retry exhausted")


def report(cur):
    return fetch_all(cur, LOG_SQL)
