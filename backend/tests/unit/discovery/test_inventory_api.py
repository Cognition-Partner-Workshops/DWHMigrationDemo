from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from migraai.discovery import ast
from migraai.discovery.inventory import Inventory
from migraai.discovery.objects import ParsedObject
from migraai.discovery.pipeline import DiscoveryEdge
from migraai.discovery.uploads import UploadRow

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


def _headers(csrf: str) -> dict[str, str]:
    return {**HOST_HEADER, "X-MigraAI-CSRF": csrf}


def test_discovery_routes_require_a_confirmed_pair(client: TestClient) -> None:
    csrf = launch(client)
    headers = _headers(csrf)
    requests = [
        ("get", "/api/uploads", {}),
        ("post", "/api/uploads", {"files": [("files", ("x.sql", b"select 1"))]}),
        ("patch", "/api/uploads/id", {"json": {"selected": True}}),
        ("delete", "/api/uploads/id", {}),
        ("get", "/api/discovery/status", {}),
        ("get", "/api/inventory", {}),
        ("get", "/api/inventory/id", {}),
        ("get", "/api/graph?object_id=id", {}),
    ]

    for method, path, kwargs in requests:
        response = getattr(client, method)(path, headers=headers, **kwargs)
        assert response.status_code == 409, path


def test_inventory_api_filters_graph_resource_counts_and_privacy(
    client: TestClient,
    data_dir: Path,
    monkeypatch,
) -> None:
    csrf = launch(client)
    headers = _headers(csrf)
    confirmed = client.post(
        "/api/session/confirm",
        json={"source": "teradata", "target": "snowflake"},
        headers=headers,
    )
    assert confirmed.status_code == 200
    session = client.app.state.current_session
    session.setdefault("upload_lock", threading.RLock())
    row = UploadRow(
        id="upload-id",
        rel_path="private_upload.sql",
        size=30,
        detected_type="sql:teradata",
        status="done",
        store_path="uploads/private",
    )
    session["uploads"][row.id] = row
    session["inventory"].add_upload(row)
    table = ParsedObject(
        type="table",
        schema=None,
        name="private_object",
        qualified_name="private_object",
        start_line=1,
        end_line=1,
        status="parsed",
        references=[],
        issues=[],
        node=ast.Unparsed(),
        file_path=row.rel_path,
    )
    view = ParsedObject(
        type="view",
        schema=None,
        name="private_view",
        qualified_name="private_view",
        start_line=2,
        end_line=2,
        status="parsed",
        references=[],
        issues=[],
        node=ast.Unparsed(),
        file_path=row.rel_path,
    )
    session["inventory"].replace_discovery(
        [table, view],
        [DiscoveryEdge("private_view", "private_object", "reads", False)],
        session["uploads"],
        {row.rel_path: "\n".join(f"line {i}" for i in range(250))},
    )
    session["inventory"].add_issue(
        row.id,
        severity="warning",
        code="test_warning",
        message="A sample issue.",
    )
    page = client.get("/api/inventory?type=table&page=1&page_size=1", headers=headers)
    assert page.status_code == 200
    assert page.json()["total"] == 1
    object_id = page.json()["items"][0]["id"]
    assert page.json()["items"][0]["upstream_count"] == 1
    detail = client.get(f"/api/inventory/{object_id}", headers=HOST_HEADER).json()
    assert len(detail["source_preview"].splitlines()) == 200
    graph = client.get(f"/api/graph?object_id={object_id}&depth=1", headers=HOST_HEADER).json()
    assert len(graph["nodes"]) == 2

    from migraai.api import resources

    monkeypatch.setattr(
        resources,
        "get_catalog",
        lambda *args, **kwargs: [{"id": "r1", "object_types": ["table"]}],
    )
    resources = client.get("/api/resources", headers=HOST_HEADER).json()
    assert resources[0]["count"] == 1
    assert resources[0]["available"] is True
    assert client.get("/api/uploads", headers=HOST_HEADER).json()[0]["issue_count"] == 1
    selected = client.patch(f"/api/uploads/{row.id}", json={"selected": False}, headers=headers)
    assert selected.status_code == 200
    assert client.get("/api/inventory?type=table", headers=HOST_HEADER).json()["total"] == 0
    assert client.get("/api/resources", headers=HOST_HEADER).json()[0]["auto_available"] is False
    assert client.delete(f"/api/uploads/{row.id}", headers=headers).json() == {"deleted": True}
    assert client.get("/api/inventory", headers=HOST_HEADER).json()["total"] == 0

    for log_path in (data_dir / "logs" / "audit.jsonl", data_dir / "logs" / "access.log"):
        contents = log_path.read_text(encoding="utf-8")
        assert "private_upload.sql" not in contents
        assert "private_object" not in contents
        assert "line 250" not in contents


def test_inventory_graph_is_capped_at_300_nodes() -> None:
    inventory = Inventory()
    row = UploadRow(
        id="upload",
        rel_path="many.sql",
        size=1,
        detected_type="sql:ansi",
        status="done",
        store_path="uploads/x",
    )
    inventory.add_upload(row)
    objects = [
        ParsedObject(
            type="table",
            schema=None,
            name=f"t{i}",
            qualified_name=f"t{i}",
            start_line=i + 1,
            end_line=i + 1,
            status="parsed",
            references=[],
            issues=[],
            node=ast.Unparsed(),
            file_path=row.rel_path,
        )
        for i in range(305)
    ]
    edges = [DiscoveryEdge("t0", f"t{i}", "reads", False) for i in range(1, 305)]
    inventory.replace_discovery(objects, edges, {row.id: row}, {})
    root = inventory.page(query="t0")["items"][0]["id"]

    graph = inventory.graph(root, 1)

    assert len(graph["nodes"]) <= 300
    assert graph["truncated"]
    inventory.close()


def test_upload_endpoint_queues_discovery_without_logging_names(
    client: TestClient, data_dir: Path
) -> None:
    csrf = launch(client)
    headers = _headers(csrf)
    assert (
        client.post(
            "/api/session/confirm",
            json={"source": "teradata", "target": "snowflake"},
            headers=headers,
        ).status_code
        == 200
    )
    response = client.post(
        "/api/uploads",
        files=[
            ("files", ("secret_filename.sql", b"CREATE TABLE secret_object(id INT)", "text/plain"))
        ],
        headers=headers,
    )
    assert response.status_code == 200
    row = response.json()["uploads"][0]
    assert row["status"] != "rejected"
    assert client.get("/api/uploads", headers=HOST_HEADER).json()[0]["id"] == row["id"]
    for _ in range(200):
        status = client.get("/api/discovery/status", headers=HOST_HEADER).json()
        if status["state"] in {"done", "failed"}:
            break
        time.sleep(0.01)
    assert status["state"] == "done"
    for path in (data_dir / "logs" / "audit.jsonl", data_dir / "logs" / "access.log"):
        contents = path.read_text(encoding="utf-8")
        assert "secret_filename.sql" not in contents
        assert "secret_object" not in contents
        assert "CREATE TABLE" not in contents
