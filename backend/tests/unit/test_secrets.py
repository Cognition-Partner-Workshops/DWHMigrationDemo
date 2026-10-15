from __future__ import annotations

import sys
from pathlib import Path

import pytest

from migraai.core.secrets import DevFileKeyProtector, DpapiProtector


def test_development_file_key_protector_round_trip_and_key_permissions(tmp_path: Path) -> None:
    protector = DevFileKeyProtector(tmp_path)
    protected = protector.protect(b"personal-access-token")
    assert protected != b"personal-access-token"
    assert protector.unprotect(protected) == b"personal-access-token"

    reopened = DevFileKeyProtector(tmp_path)
    assert reopened.unprotect(protected) == b"personal-access-token"
    if sys.platform != "win32":
        assert (tmp_path / "dev-secret.key").stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only.")
def test_dpapi_protector_round_trip() -> None:
    protector = DpapiProtector()
    protected = protector.protect(b"personal-access-token")
    assert protector.unprotect(protected) == b"personal-access-token"
