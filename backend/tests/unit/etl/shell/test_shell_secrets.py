"""Regression: no credential from design §5 may leak into objects/SQL/issues."""

from __future__ import annotations

import json

from migraai.etl.airflow.parse import parse as airflow_parse
from migraai.etl.python.parse import parse as python_parse
from migraai.etl.shell.parse import parse as shell_parse
from migraai.etl.spark.parse import parse as spark_parse

SECRETS = ["Zebra77", "hunter2", "s3cret", "p4ss", "tok_abc", "k3y"]


def _dump(result) -> str:
    return (
        json.dumps([o.to_dict() for o in result.objects])
        + json.dumps([e.text for e in result.embedded_sql])
        + json.dumps(
            [
                i.to_dict() if hasattr(i, "to_dict") else i.message
                for o in result.objects
                for i in o.issues
            ]
        )
        + json.dumps([i.message for i in result.issues])
    )


def test_shell_no_secret_leaks() -> None:
    script = """\
export DB_PASSWORD=Zebra77
DB_PASSWORD=Zebra77
local PWD_X=hunter2
readonly API_TOKEN=tok_abc
bteq <<'EOS'
.LOGON tdw1/etluser,s3cret
SELECT 1;
EOS
sqlplus etl/s3cretpw@proddb @x.sql
bcp t in f -P hunter2
nzsql -w s3cret -c 'SELECT 1'
db2 "connect to d user u using s3cret"
beeline -p hunter2 -e 'SELECT 1'
mysql -u root -ps3cret -e 'SELECT 1'
prog --password hunter2
prog --password=s3cret
curl https://user:s3cret@host/api
"""
    payload = _dump(shell_parse(script.encode(), "bin/j.sh"))
    for secret in SECRETS + ["s3cretpw"]:
        assert secret not in payload, secret
    assert "****" in payload


def test_python_no_secret_leaks() -> None:
    src = b"""\
import pyodbc
CONN = "Server=h;PWD=hunter2;UID=u"
DB_PASSWORD = "Zebra77"
def f():
    pyodbc.connect("DRIVER=x;SERVER=h;PWD=s3cret")
    connect(host='h', password='hunter2', api_key='k3y', token='tok_abc')
"""
    payload = _dump(python_parse(src, "m/x.py"))
    for secret in SECRETS:
        assert secret not in payload, secret


def test_spark_no_secret_leaks() -> None:
    src = b"""\
def f(spark):
    spark.read.jdbc("jdbc:x", "t", properties={"password": "hunter2"})
    spark.conf.set("spark.sql.password", "s3cret")
"""
    payload = _dump(spark_parse(src, "j/x.py"))
    for secret in SECRETS:
        assert secret not in payload, secret


def test_airflow_no_secret_leaks() -> None:
    src = b"""\
from airflow import DAG
from airflow.operators.bash import BashOperator
with DAG("d") as dag:
    t = BashOperator(task_id="t", bash_command="sqlplus u/s3cretpw@db @x.sql")
"""
    payload = _dump(airflow_parse(src, "dags/d.py"))
    for secret in ["s3cretpw", "s3cret"]:
        assert secret not in payload, secret
