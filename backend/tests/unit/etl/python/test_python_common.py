from __future__ import annotations

import json

from migraai.discovery import ast
from migraai.etl.python.common import (
    EtlBuilder,
    decode,
    file_unparsed,
    normalize_path,
    qname,
    redact,
    span,
)


def test_decode_utf8_sig_and_crlf() -> None:
    assert decode(b"\xef\xbb\xbfa\r\nb\rc\n") == "a\nb\nc\n"


def test_qname_split_and_quoting() -> None:
    name = qname("sales.[order items].`weird`")
    assert [p.value for p in name.parts] == ["sales", "order items", "weird"]
    assert name.parts[1].quoted is True
    assert name.parts[0].quoted is False
    single = qname("dir/sub dir/file.sql", split=False)
    assert len(single.parts) == 1 and single.parts[0].value == "dir/sub dir/file.sql"


def test_span_defaults() -> None:
    assert span(3) == ast.Span(3, 1, 3, 1)
    assert span(3, 5) == ast.Span(3, 1, 5, 1)


def test_redact_connection_strings() -> None:
    assert redact("password=hunter2;") == "password=****;"
    assert redact("PWD = abc") == "PWD = ****"
    assert redact("Server=x;Password=top secret".replace(" ", ";")) == "Server=x;Password=****"
    assert redact("jdbc://user:pw@host/db") == "jdbc://user:****@host/db"


def test_redact_tool_forms() -> None:
    assert redact(".LOGON tdpid/etluser,s3cret") == ".LOGON tdpid/etluser,****"
    assert redact("sqlplus user/p4ss@db @f.sql") == "sqlplus user/****@db @f.sql"
    assert redact("db2 connect to DWH user etl using s3cret") == (
        "db2 connect to DWH user etl using ****"
    )
    assert redact("bcp t in f -P secret -U u") == "bcp t in f -P **** -U u"
    assert redact("nzsql -w nzpw -c 'select 1'") == "nzsql -w **** -c 'select 1'"
    assert redact("beeline -p hivepw -e 'select 1'") == "beeline -p **** -e 'select 1'"
    assert redact("--password=zzz") == "--password=****"
    assert redact("--password zzz") == "--password ****"
    assert redact("MY_PASSWORD=hunter2") == "MY_PASSWORD=****"
    assert redact("API_TOKEN=tok123") == "API_TOKEN=****"


def test_redact_idempotent() -> None:
    once = redact("password=hunter2 --pw x")
    assert redact(once) == once
    assert "hunter2" not in once


def test_normalize_path() -> None:
    rel = "bin/run.sh"
    assert normalize_path("./x.sh", rel) == "bin/x.sh"
    assert normalize_path("x.sh", rel) == "bin/x.sh"
    assert normalize_path("/abs/x.sh", rel) == "/abs/x.sh"
    assert normalize_path("$ETL_HOME/x.sh", rel, {"ETL_HOME": "/opt"}) == "/opt/x.sh"
    assert normalize_path("${ETL_HOME}/x.sh", rel, {"ETL_HOME": "sub"}) == "bin/sub/x.sh"
    assert normalize_path("$UNRESOLVED/x.sh", rel) == "x.sh"
    assert normalize_path("$(dirname $0)/x.sh", rel) == "bin/x.sh"
    assert normalize_path('$(dirname "$0")/x.sh', rel) == "bin/x.sh"
    assert normalize_path("${0%/*}/x.sh", rel) == "bin/x.sh"
    assert normalize_path("%%CTM_DIR/x.sh", rel, {"CTM_DIR": "jobs"}) == "bin/jobs/x.sh"
    assert normalize_path("../other/x.sh", rel) == "other/x.sh"


def test_file_unparsed() -> None:
    obj = file_unparsed("d/f.xml", "bad xml")
    assert obj.type == "file" and obj.status == "unparsed"
    assert obj.qualified_name == "d/f.xml"
    assert isinstance(obj.node, ast.Unparsed) and obj.node.text == ""
    assert obj.issues[0].severity == "error"


def test_builder_status_and_refs() -> None:
    builder = EtlBuilder("python", "a/b.py")
    node = ast.DialectStatement(
        name="python.thing",
        args={},
        references=[
            builder.ref("a.b", "etl_reads", span(1)),
            builder.ref("a.b", "etl_reads", span(1)),
            builder.ref("c", "calls", span(1)),
        ],
    )
    obj = builder.add("module", "a.b", node, 1, 5)
    assert obj.status == "parsed" and obj.schema == "a" and obj.name == "b"
    assert [(r.ref_kind) for r in obj.references] == ["etl_reads", "calls"]

    bad = ast.DialectStatement(
        name="python.thing",
        args={"inner": ast.Unparsed(text="x", reason="bad")},
    )
    obj = builder.add("module", "a.c", bad, 1, 2)
    assert obj.status == "partial"

    obj = builder.add("module", "a.d", ast.Unparsed(text="", reason="nope"), 1, 1)
    assert obj.status == "unparsed"

    err_issue = builder.issue("error", "broken", "boom", span(2))
    ok = ast.DialectStatement(name="python.thing", args={})
    obj = builder.add("module", "a.e", ok, 2, 4, issues=[err_issue])
    assert obj.status == "partial"


def test_builder_embed_redacts_and_skips_empty() -> None:
    builder = EtlBuilder("shell", "s.sh")
    builder.embed("s.sh", "  ", "x")
    assert builder.result().embedded_sql == []
    builder.embed("s.sh", "call user/pw@db", "sqlplus")
    (embedded,) = builder.result().embedded_sql
    assert "pw@db" not in embedded.text and "****" in embedded.text
    assert embedded.location == "s.sh#s.sh:sqlplus"


def test_no_secret_leaks_in_object_json() -> None:
    builder = EtlBuilder("shell", "s.sh")
    builder.embed("s.sh", "CONNECT user/secret@db", "sqlplus")
    node = ast.DialectStatement(
        name="shell.command",
        args={"program": ast.Identifier(value="sqlplus")},
        references=[],
    )
    obj = builder.add("script", "s.sh", node, 1, 3)
    payload = json.dumps(obj.to_dict()) + json.dumps(
        [e.text for e in builder.result().embedded_sql]
    )
    assert "secret" not in payload
