from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from migraai.core.crypto import EncryptedFileStore, SessionCipher


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
