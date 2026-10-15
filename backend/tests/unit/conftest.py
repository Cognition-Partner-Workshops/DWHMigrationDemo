from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migraai.main import create_app

HOST_HEADER = {"Host": "127.0.0.1:80"}


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def app(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MIGRAAI_DEV_TOKEN", "test-launch-token")
    return create_app(data_dir=data_dir)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://127.0.0.1:80") as test_client:
        yield test_client


def launch(client: TestClient) -> str:
    response = client.get(
        "/api/auth/launch",
        params={"token": "test-launch-token"},
        headers=HOST_HEADER,
        follow_redirects=False,
    )
    assert response.status_code == 303
    return str(client.cookies["migraai_csrf"])
