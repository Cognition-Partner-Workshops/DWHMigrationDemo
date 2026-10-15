from __future__ import annotations

from pathlib import Path

import pytest

from migraai.discovery.detect import decode_source, detect_file


@pytest.mark.parametrize(
    ("path", "content", "expected"),
    [
        ("a.sql", b"select 1", "sql:teradata"),
        ("a.ddl", b"create table t (id int)", "sql:teradata"),
        ("a.txt", b"select 1", "sql:teradata"),
        ("a.bteq", b"select 1", "sql:teradata"),
        ("a.xml", b"<POWERMART/>", "informatica"),
        ("a.xml", b"<REPOSITORY/>", "informatica"),
        ("a.dsx", b"anything", "datastage"),
        ("a.xml", b"<DSExport/>", "datastage"),
        ("a.dtsx", b"anything", "ssis"),
        ("a.xml", b'<DTS:Executable xmlns:DTS="urn:dts"/>', "ssis"),
        ("a.xml", b"<DEFTABLE/>", "controlm"),
        ("a.json", b'{"definitions":{"jobs":[]}}', "controlm"),
        ("a.json", b'{"definitions":{"jobs":[]},"file":"submit.py"}', "controlm"),
        ("a.json", b'{"className":"com.acme.Job"}', "spark"),
        ("a.yml", b"dag:\n  tasks:\n    extract: {}\n", "airflow"),
        ("a.yaml", b"dag:\n  default_args: {}\n", "airflow"),
        ("a.py", b"from airflow import DAG\ndag = DAG('x')", "airflow"),
        ("a.py", b"import pyspark.sql", "spark"),
        ("a.py", b"print('ordinary')", "python"),
        ("a.scala", b"val spark = SparkSession.builder.getOrCreate()", "spark"),
        ("a.sh", b"echo ready", "shell"),
        ("a.xml", b"<unknown/>", "unsupported"),
        ("a.bin", b"data", "unsupported"),
    ],
)
def test_detection_table(path: str, content: bytes, expected: str) -> None:
    assert detect_file(path, content, "teradata").detected_type == expected


def test_go_lines_warn_for_teradata() -> None:
    result = detect_file("batch.sql", b"select 1\nGO\nselect 2", "teradata")

    assert result.detected_type == "sql:teradata"
    assert any("GO batch" in issue for issue in result.issues)


def test_source_decoding_boms_and_cp1252_fallback() -> None:
    assert decode_source(b"\xef\xbb\xbfselect 1")[1] == "utf-8-sig"
    assert decode_source(b"\xff\xfe" + "select 1".encode("utf-16-le"))[1] == "utf-16"
    text, encoding, issues = decode_source("café".encode("cp1252"))
    assert (text, encoding) == ("café", "cp1252")
    assert issues


def test_xxe_input_is_not_expanded() -> None:
    payload = b"""<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]>
    <x>&e;</x>"""

    detected = detect_file("unsafe.xml", payload, "teradata")

    assert detected.detected_type == "unsupported"
    assert "<x>&e;</x>" in (detected.text or "")


def test_python_detection_never_executes_uploaded_source(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n"

    assert detect_file("source.py", source.encode(), "teradata").detected_type == "python"
    assert not marker.exists()
