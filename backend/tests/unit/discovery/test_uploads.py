from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from migraai.core.crypto import EncryptedFileStore, SessionCipher
from migraai.discovery.uploads import ingest_file


def _session(tmp_path: Path) -> dict:
    return {
        "source": "teradata",
        "store": EncryptedFileStore(tmp_path / "session", SessionCipher()),
        "uploads": {},
        "upload_bytes_received": 0,
        "zip_uncompressed_bytes": 0,
    }


def _zip(entries: list[tuple[str, bytes, int | None]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if mode is None:
                archive.writestr(info, data)
            else:
                info.create_system = 3
                info.external_attr = mode << 16
                archive.writestr(info, data)
    return output.getvalue()


def test_upload_streams_in_bounded_chunks_and_encrypts_at_rest(tmp_path: Path) -> None:
    class CountingStream(io.BytesIO):
        largest_read = 0

        def read(self, size: int = -1) -> bytes:
            self.largest_read = max(self.largest_read, size)
            return super().read(size)

    session = _session(tmp_path)
    stream = CountingStream(b"create table t(id int)")
    row = ingest_file("t.sql", stream, session)[0]

    encrypted = (tmp_path / "session" / row.store_path).read_bytes()
    assert row.status == "pending"
    assert stream.largest_read <= 64 * 1024
    assert b"create table" not in encrypted
    assert session["store"].read(row.store_path) == b"create table t(id int)"


def test_upload_limit_is_enforced_mid_stream(tmp_path: Path, monkeypatch) -> None:
    import migraai.discovery.uploads as uploads

    monkeypatch.setattr(uploads, "MAX_SESSION_BYTES", 8)
    row = ingest_file("large.sql", io.BytesIO(b"123456789"), _session(tmp_path))[0]

    assert row.status == "rejected"
    assert "size limit" in (row.message or "")


def test_extension_spoof_is_rejected(tmp_path: Path) -> None:
    row = ingest_file("script.exe", io.BytesIO(b"select 1"), _session(tmp_path))[0]

    assert row.status == "rejected"
    assert "extension" in (row.message or "")


@pytest.mark.parametrize("zip_member", [False, True])
def test_xml_declarations_are_rejected_before_storage(tmp_path: Path, zip_member: bool) -> None:
    session = _session(tmp_path)
    payload = b"""<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]>
    <x>&e;</x>"""
    filename = "unsafe.xml"
    content = payload
    if zip_member:
        filename = "archive.zip"
        content = _zip([("unsafe.xml", payload, None)])

    row = ingest_file(filename, io.BytesIO(content), session)[0]

    assert row.status == "rejected"
    assert row.message == (
        "XML with DOCTYPE or entity declarations is not accepted for security reasons."
    )
    assert not row.store_path
    assert not (session["store"].directory / "uploads").exists()


@pytest.mark.parametrize(
    ("filename", "content", "detected_type"),
    [
        ("informatica.xml", b"<POWERMART/>", "informatica"),
        ("ssis.xml", b'<DTS:Executable xmlns:DTS="urn:dts"/>', "ssis"),
    ],
)
def test_normal_etl_xml_is_accepted(
    tmp_path: Path, filename: str, content: bytes, detected_type: str
) -> None:
    session = _session(tmp_path)

    row = ingest_file(filename, io.BytesIO(content), session)[0]

    assert row.status == "pending"
    assert row.detected_type == detected_type
    assert row.store_path
    assert session["store"].read(row.store_path) == content


def test_disallowed_extension_inside_zip_is_rejected(tmp_path: Path) -> None:
    rows = ingest_file(
        "archive.zip",
        io.BytesIO(_zip([("script.exe", b"not a source", None)])),
        _session(tmp_path),
    )

    assert rows[0].status == "rejected"
    assert "extension" in (rows[0].message or "")


@pytest.mark.parametrize(
    "entry",
    ["../escape.sql", "/absolute.sql", "C:\\escape.sql", "dir\\..\\escape.sql"],
)
def test_zip_slip_variants_are_rejected(tmp_path: Path, entry: str) -> None:
    rows = ingest_file(
        "archive.zip", io.BytesIO(_zip([(entry, b"select 1", None)])), _session(tmp_path)
    )

    assert rows and all(row.status == "rejected" for row in rows)


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    rows = ingest_file(
        "archive.zip",
        io.BytesIO(_zip([("link.sql", b"target", stat.S_IFLNK | 0o777)])),
        _session(tmp_path),
    )

    assert rows[0].status == "rejected"


def test_zip_ratio_limit_is_enforced(tmp_path: Path) -> None:
    rows = ingest_file(
        "archive.zip",
        io.BytesIO(_zip([("large.sql", b"x" * 50_000, None)])),
        _session(tmp_path),
    )

    assert rows[0].status == "rejected"
    assert "compression ratio" in (rows[0].message or "")


def test_zip_total_uncompressed_limit_is_enforced(tmp_path: Path, monkeypatch) -> None:
    import migraai.discovery.uploads as uploads

    monkeypatch.setattr(uploads, "MAX_ZIP_UNCOMPRESSED", 8)
    rows = ingest_file(
        "archive.zip",
        io.BytesIO(_zip([("large.sql", b"123456789", None)])),
        _session(tmp_path),
    )

    assert rows[0].status == "rejected"


def test_zip_total_limit_counts_bytes_from_rejected_entries(tmp_path: Path, monkeypatch) -> None:
    import migraai.discovery.uploads as uploads

    monkeypatch.setattr(uploads, "MAX_ZIP_UNCOMPRESSED", 8)
    session = _session(tmp_path)
    rows = ingest_file(
        "archive.zip",
        io.BytesIO(_zip([("too-large.sql", b"123456789", None), ("later.sql", b"x", None)])),
        session,
    )

    assert [row.status for row in rows] == ["rejected", "rejected"]
    assert session["zip_uncompressed_bytes"] > 8


def test_zip_entry_count_limit_is_enforced(tmp_path: Path, monkeypatch) -> None:
    import migraai.discovery.uploads as uploads

    monkeypatch.setattr(uploads, "MAX_ZIP_ENTRIES", 1)
    archive = _zip([("a.sql", b"a", None), ("b.sql", b"b", None)])

    assert (
        ingest_file("archive.zip", io.BytesIO(archive), _session(tmp_path))[0].status == "rejected"
    )


def test_one_nested_zip_is_allowed_but_a_second_is_rejected(tmp_path: Path) -> None:
    deepest = _zip([("data.sql", b"select 1", None)])
    nested = _zip([("deeper.zip", deepest, None)])
    outer = _zip([("nested.zip", nested, None)])

    rows = ingest_file("outer.zip", io.BytesIO(outer), _session(tmp_path))

    assert rows and rows[0].status == "rejected"


def test_single_nested_zip_extracts_allowed_entry(tmp_path: Path) -> None:
    nested = _zip([("data.sql", b"select 1", None)])
    outer = _zip([("nested.zip", nested, None)])

    rows = ingest_file("outer.zip", io.BytesIO(outer), _session(tmp_path))

    assert len(rows) == 1
    assert rows[0].rel_path.endswith("nested.zip/data.sql")
    assert rows[0].status == "pending"
