import os

import pytest
from fastapi.testclient import TestClient

from migraai.kb.embed import FastEmbedEmbedder

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


def test_kb_auth_status_search_and_csrf(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("MIGRAAI_EMBEDDER", "hash")
    assert client.get("/api/kb/status", headers=HOST_HEADER).status_code == 401
    csrf = launch(client)
    status = client.get("/api/kb/status", headers=HOST_HEADER)
    assert status.status_code == 200
    assert status.json()["current_version"] == 1
    assert client.get("/api/kb/search?q=data%20types", headers=HOST_HEADER).status_code == 200
    assert client.post("/api/kb/refresh", headers=HOST_HEADER).status_code == 403
    assert (
        client.post(
            "/api/kb/refresh",
            headers={**HOST_HEADER, "X-MigraAI-CSRF": csrf},
        ).status_code
        == 202
    )
    client.app.state.kb_refresh_job.thread.join(timeout=5)
    assert client.app.state.kb_refresh_job.done


def test_kb_refresh_and_rollback_conflicts(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("MIGRAAI_EMBEDDER", "hash")
    csrf = launch(client)
    app = client.app
    app.state.kb_refresh_job = type("Running", (), {"done": False})()
    headers = {**HOST_HEADER, "X-MigraAI-CSRF": csrf}
    assert client.post("/api/kb/refresh", headers=headers).status_code == 409
    assert client.post("/api/kb/rollback", headers=headers).status_code == 409
    app.state.kb_refresh_job = None


def test_kb_rollback_endpoint_activates_previous_version(client: TestClient) -> None:
    csrf = launch(client)
    client.app.state.kb_store.create_empty(2)

    response = client.post(
        "/api/kb/rollback",
        headers={**HOST_HEADER, "X-MigraAI-CSRF": csrf},
    )

    assert response.status_code == 200
    assert response.json() == {"current_version": 1}


@pytest.mark.model
@pytest.mark.skipif(
    os.environ.get("MIGRAAI_RUN_MODEL_TEST") != "1",
    reason="Set MIGRAAI_RUN_MODEL_TEST=1 to run the real FastEmbed model.",
)
def test_model_embedding_is_opt_in_and_384_dim() -> None:
    vectors = FastEmbedEmbedder().embed(["model dimension smoke test"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 384
