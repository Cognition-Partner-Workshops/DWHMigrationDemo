from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from migraai.core.crypto import (
    _STREAM_MAGIC,
    _STREAM_RECORD,
    EncryptedFileStore,
    SessionCipher,
)


def stream_records(data: bytes) -> list[bytes]:
    records = []
    position = len(_STREAM_MAGIC)
    while position < len(data):
        start = position
        size, _final = _STREAM_RECORD.unpack(data[position : position + _STREAM_RECORD.size])
        position += _STREAM_RECORD.size
        position += size + 28
        records.append(data[start:position])
    return records


def test_session_cipher_round_trip_and_authentication() -> None:
    cipher = SessionCipher()
    encrypted = cipher.encrypt(b"confidential", aad=b"object-1")

    assert encrypted[:12]
    assert cipher.decrypt(encrypted, aad=b"object-1") == b"confidential"
    with pytest.raises(InvalidTag):
        cipher.decrypt(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), aad=b"object-1")
    with pytest.raises(InvalidTag):
        cipher.decrypt(encrypted, aad=b"object-2")


def test_encrypted_file_store_round_trip_and_rejects_traversal(tmp_path: Path) -> None:
    store = EncryptedFileStore(tmp_path / "session", SessionCipher())
    store.write("uploads/source.sql", b"select confidential_data")

    assert store.read("uploads/source.sql") == b"select confidential_data"
    assert (tmp_path / "session" / "uploads" / "source.sql").read_bytes() != (
        b"select confidential_data"
    )
    with pytest.raises(ValueError):
        store.write("../outside.txt", b"no")
    with pytest.raises(ValueError):
        store.read(tmp_path / "outside.txt")


def test_encrypted_file_store_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    store = EncryptedFileStore(tmp_path / "session", SessionCipher())
    link = store.directory / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable.")

    with pytest.raises(ValueError):
        store.write("escape/secret.txt", b"no")


def test_streamed_file_round_trip_with_three_chunks(tmp_path: Path) -> None:
    store = EncryptedFileStore(tmp_path / "session", SessionCipher())

    assert store.write_stream("three-chunks", BytesIO(b"abcdefghijkl"), chunk_size=4) == 12

    encrypted = (store.directory / "three-chunks").read_bytes()
    records = stream_records(encrypted)
    assert len(records) == 3
    assert [_STREAM_RECORD.unpack(record[: _STREAM_RECORD.size])[1] for record in records] == [
        0,
        0,
        1,
    ]
    assert store.read("three-chunks") == b"abcdefghijkl"


def test_streamed_empty_file_has_one_authenticated_final_record(tmp_path: Path) -> None:
    store = EncryptedFileStore(tmp_path / "session", SessionCipher())

    assert store.write_stream("empty", BytesIO(b""), chunk_size=4) == 0

    encrypted = (store.directory / "empty").read_bytes()
    records = stream_records(encrypted)
    assert len(records) == 1
    assert _STREAM_RECORD.unpack(records[0][: _STREAM_RECORD.size]) == (0, 1)
    assert store.read("empty") == b""


def test_streamed_file_rejects_truncation_of_final_record(tmp_path: Path) -> None:
    store = EncryptedFileStore(tmp_path / "session", SessionCipher())
    store.write_stream("truncated", BytesIO(b"abcdefgh"), chunk_size=4)
    path = store.directory / "truncated"
    encrypted = path.read_bytes()
    path.write_bytes(_STREAM_MAGIC + stream_records(encrypted)[0])

    with pytest.raises(ValueError, match="truncated"):
        store.read("truncated")


def test_streamed_file_rejects_reordered_records(tmp_path: Path) -> None:
    store = EncryptedFileStore(tmp_path / "session", SessionCipher())
    store.write_stream("reordered", BytesIO(b"abcdefgh"), chunk_size=4)
    path = store.directory / "reordered"
    encrypted = path.read_bytes()
    records = stream_records(encrypted)
    path.write_bytes(_STREAM_MAGIC + records[1] + records[0])

    with pytest.raises(InvalidTag):
        store.read("reordered")


def test_streamed_file_rejects_appended_record(tmp_path: Path) -> None:
    store = EncryptedFileStore(tmp_path / "session", SessionCipher())
    store.write_stream("appended", BytesIO(b"abcdefgh"), chunk_size=4)
    path = store.directory / "appended"
    encrypted = path.read_bytes()
    path.write_bytes(encrypted + stream_records(encrypted)[-1])

    with pytest.raises(ValueError, match="after its final record"):
        store.read("appended")


def test_legacy_encrypted_file_still_decrypts(tmp_path: Path) -> None:
    store = EncryptedFileStore(tmp_path / "session", SessionCipher())
    store.write("legacy", b"legacy file data")

    assert store.read("legacy") == b"legacy file data"
