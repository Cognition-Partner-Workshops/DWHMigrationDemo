from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migraai.api import session as session_api
from migraai.core.security import AuthState
from migraai.core.storage import create_session_dir
from migraai.main import create_app

HOST_HEADER = {"Host": "127.0.0.1:80"}


def launch(client: TestClient) -> str:
    response = client.get(
        "/api/auth/launch",
        params={"token": "test-launch-token"},
        headers=HOST_HEADER,
        follow_redirects=False,
    )
    assert response.status_code == 303
    return str(client.cookies["migraai_csrf"])


def test_health_is_open_and_returns_local_status(client: TestClient) -> None:
    response = client.get("/api/health", headers=HOST_HEADER)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "mode": "local",
        "version": "0.1.0",
        "devin_configured": False,
    }


def test_host_guard_rejects_rebinding(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Host": "malicious.example:80"})

    assert response.status_code == 400


def test_api_requires_launch_cookie_and_launch_token_is_single_use(
    client: TestClient,
) -> None:
    assert client.get("/api/settings", headers=HOST_HEADER).status_code == 401

    response = client.get(
        "/api/auth/launch",
        params={"token": "test-launch-token"},
        headers=HOST_HEADER,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "httponly" in response.headers["set-cookie"].casefold()
    assert client.get("/api/settings", headers=HOST_HEADER).status_code == 200
    assert (
        client.get(
            "/api/auth/launch",
            params={"token": "test-launch-token"},
            headers=HOST_HEADER,
            follow_redirects=False,
        ).status_code
        == 403
    )


def test_launch_token_uses_dev_override(monkeypatch) -> None:
    monkeypatch.setenv("MIGRAAI_DEV_TOKEN", "local-test-token")
    assert AuthState().launch_token == "local-test-token"


def test_settings_pat_is_masked_and_never_written_to_logs_or_database(
    client: TestClient,
    data_dir: Path,
) -> None:
    csrf = launch(client)
    response = client.put(
        "/api/settings",
        headers={**HOST_HEADER, "X-MigraAI-CSRF": csrf},
        json={
            "devin_pat": "secret-personal-access-token",
            "devin_org_id": "org-demo",
            "devin_api_base_url": "https://api.devin.ai",
            "confidence_threshold": 0.9,
            "max_concurrent_sessions": 6,
            "acu_cap_per_run": 25,
        },
    )

    assert response.status_code == 200
    assert response.json()["devin_pat"] == {"configured": True, "masked": "••••oken"}
    assert b"secret-personal-access-token" not in (data_dir / "app.db").read_bytes()
    for log_path in (
        data_dir / "logs" / "access.log",
        data_dir / "logs" / "audit.jsonl",
    ):
        assert b"secret-personal-access-token" not in log_path.read_bytes()
    audit = (data_dir / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    expected_fields = (
        '"fields":["acu_cap_per_run","confidence_threshold","devin_api_base_url","devin_org_id",'
        '"devin_pat","max_concurrent_sessions"]'
    )
    assert expected_fields in audit
    assert '"secret-personal-access-token"' not in audit


def test_settings_validation_rejects_out_of_range_values(client: TestClient) -> None:
    csrf = launch(client)
    response = client.put(
        "/api/settings",
        headers={**HOST_HEADER, "X-MigraAI-CSRF": csrf},
        json={"confidence_threshold": 0.4},
    )

    assert response.status_code == 422


def test_csrf_is_required_for_mutations(client: TestClient) -> None:
    csrf = launch(client)
    body = {"source": "teradata", "target": "databricks"}

    missing = client.post("/api/session/confirm", json=body, headers=HOST_HEADER)
    wrong = client.post(
        "/api/session/confirm",
        json=body,
        headers={**HOST_HEADER, "X-MigraAI-CSRF": "wrong"},
    )
    accepted = client.post(
        "/api/session/confirm",
        json=body,
        headers={**HOST_HEADER, "X-MigraAI-CSRF": csrf},
    )
    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert accepted.status_code == 200
    assert set(accepted.json()) == {"session_id", "source", "target"}


def test_platforms_and_session_validation(client: TestClient) -> None:
    csrf = launch(client)
    platforms = client.get("/api/platforms", headers=HOST_HEADER).json()
    assert len(platforms["sources"]) == 8
    assert len(platforms["targets"]) == 7

    headers = {**HOST_HEADER, "X-MigraAI-CSRF": csrf}
    unsupported = client.post(
        "/api/session/confirm",
        json={"source": "unknown", "target": "databricks"},
        headers=headers,
    )
    same = client.post(
        "/api/session/confirm",
        json={"source": "snowflake", "target": "snowflake"},
        headers=headers,
    )
    assert unsupported.status_code == 422
    assert same.status_code == 422


def test_reconfirm_wipes_only_previous_session_and_audits_replacement(
    client: TestClient,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = launch(client)
    headers = {**HOST_HEADER, "X-MigraAI-CSRF": csrf}
    payload = {"source": "teradata", "target": "databricks"}
    first = client.post("/api/session/confirm", json=payload, headers=headers)
    previous_session_id = first.json()["session_id"]
    previous_session_dir = data_dir / "sessions" / previous_session_id
    (previous_session_dir / "previous.txt").write_text("remove me", encoding="utf-8")

    unrelated_session_dir = create_session_dir(data_dir)
    unrelated_file = unrelated_session_dir / "keep.txt"
    unrelated_file.write_text("keep me", encoding="utf-8")

    original_create_session_dir = session_api.create_session_dir

    def create_after_previous_session_is_wiped(root: Path) -> Path:
        assert not previous_session_dir.exists()
        return original_create_session_dir(root)

    monkeypatch.setattr(session_api, "create_session_dir", create_after_previous_session_is_wiped)
    second = client.post("/api/session/confirm", json=payload, headers=headers)
    replacement_session_id = second.json()["session_id"]

    assert second.status_code == 200
    assert replacement_session_id != previous_session_id
    assert not previous_session_dir.exists()
    assert (data_dir / "sessions" / replacement_session_id).is_dir()
    assert unrelated_file.read_text(encoding="utf-8") == "keep me"

    audit_records = [
        json.loads(line)
        for line in (data_dir / "logs" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    replacement = next(record for record in audit_records if record["action"] == "session.replace")
    assert replacement["target"] == previous_session_id
    assert replacement["details"] == {"new_session_id": replacement_session_id}


def test_access_log_omits_query_string_and_session_cookie(
    client: TestClient, data_dir: Path
) -> None:
    launch(client)
    client.get("/api/health", params={"probe": "value"}, headers=HOST_HEADER)
    access_log = (data_dir / "logs" / "access.log").read_text(encoding="utf-8")
    assert "?" not in access_log
    assert "launch=" not in access_log
    assert "test-launch-token" not in access_log
    assert "migraai_session" not in access_log
    assert all("path" in json.loads(line) for line in access_log.splitlines())


def test_spa_serves_index_for_unknown_non_api_paths(tmp_path: Path) -> None:
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<main>MigraAI</main>", encoding="utf-8")
    app = create_app(web_dir=web_dir, data_dir=tmp_path / "data")

    with TestClient(app, base_url="http://127.0.0.1:80") as client:
        response = client.get("/settings")
        assert response.status_code == 200
        assert response.text == "<main>MigraAI</main>"
