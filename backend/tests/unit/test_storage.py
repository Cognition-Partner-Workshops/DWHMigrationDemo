from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from migraai.core.storage import create_session_dir, wipe_sessions
from migraai.main import create_app


def test_create_session_dir_and_wipe(tmp_path: Path) -> None:
    session_dir = create_session_dir(tmp_path)
    assert session_dir.parent == tmp_path / "sessions"
    assert session_dir.is_dir()
    if os.name != "nt":
        assert session_dir.stat().st_mode & 0o777 == 0o700

    wipe_sessions(tmp_path)
    assert list((tmp_path / "sessions").iterdir()) == []


def test_startup_sweeper_removes_crash_leftovers(tmp_path: Path, monkeypatch) -> None:
    stale = tmp_path / "sessions" / "leftover"
    stale.mkdir(parents=True)
    (stale / "upload.enc").write_bytes(b"old")
    monkeypatch.setenv("MIGRAAI_DEV_TOKEN", "test-launch-token")
    app = create_app(data_dir=tmp_path)

    with TestClient(app, base_url="http://127.0.0.1:80"):
        assert list((tmp_path / "sessions").iterdir()) == []

    assert list((tmp_path / "sessions").iterdir()) == []
