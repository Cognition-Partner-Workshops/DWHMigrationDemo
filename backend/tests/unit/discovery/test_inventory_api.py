from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import zipfile
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
    external = ParsedObject(
        type="external",
        schema=None,
        name="private_external",
        qualified_name="private_external",
        start_line=3,
        end_line=3,
        status="external",
        references=[],
        issues=[],
        node=ast.Unparsed(),
        file_path=row.rel_path,
    )
    session["inventory"].replace_discovery(
        [table, view, external],
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
    assert page.json()["external_total"] == 0
    all_page = client.get("/api/inventory?page_size=50", headers=headers).json()
    assert all_page["total"] == 3
    assert all_page["external_total"] == 1
    external_page = client.get("/api/inventory?type=external", headers=headers).json()
    assert external_page["total"] == 1
    assert external_page["external_total"] == 1
    assert client.get("/api/inventory?status=parsed", headers=headers).json()["external_total"] == 0
    assert (
        client.get("/api/inventory?file=private_upload.sql", headers=headers).json()[
            "external_total"
        ]
        == 1
    )
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
    assert client.get("/api/inventory", headers=HOST_HEADER).json()["external_total"] == 0
    assert client.get("/api/resources", headers=HOST_HEADER).json()[0]["auto_available"] is False
    assert client.delete(f"/api/uploads/{row.id}", headers=headers).json() == {"deleted": True}
    assert client.get("/api/inventory", headers=HOST_HEADER).json()["total"] == 0

    audit_records = [
        json.loads(line)
        for line in (data_dir / "logs" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        record["action"] == "uploads.deleted" and record["details"]["count"] == 1
        for record in audit_records
    )
    for log_path in (data_dir / "logs" / "audit.jsonl", data_dir / "logs" / "access.log"):
        contents = log_path.read_text(encoding="utf-8")
        assert "private_upload.sql" not in contents
        assert "private_object" not in contents
        assert "line 250" not in contents


def test_inventory_aggregates_selected_objects_and_fingerprint() -> None:
    inventory = Inventory()
    empty_fingerprint = inventory.fingerprint()
    selected = UploadRow(
        id="selected",
        rel_path="selected.sql",
        size=20,
        detected_type="sql:ansi",
        status="done",
        store_path="uploads/selected",
    )
    deselected = UploadRow(
        id="deselected",
        rel_path="deselected.sql",
        size=15,
        detected_type="sql:ansi",
        status="done",
        selected=False,
        store_path="uploads/deselected",
    )
    uploads = {selected.id: selected, deselected.id: deselected}
    inventory.add_upload(selected)
    inventory.add_upload(deselected)
    uploaded_fingerprint = inventory.fingerprint()
    assert uploaded_fingerprint
    assert uploaded_fingerprint != empty_fingerprint
    objects = [
        ParsedObject(
            type="table",
            schema=None,
            name="customers",
            qualified_name="customers",
            start_line=1,
            end_line=2,
            status="parsed",
            references=[],
            issues=[],
            node=ast.Unparsed(),
            file_path=selected.rel_path,
        ),
        ParsedObject(
            type="view",
            schema=None,
            name="v_summary",
            qualified_name="v_summary",
            start_line=3,
            end_line=4,
            status="partial",
            references=[],
            issues=[],
            node=ast.Unparsed(),
            file_path=selected.rel_path,
        ),
        ParsedObject(
            type="function",
            schema=None,
            name="ignored_function",
            qualified_name="ignored_function",
            start_line=1,
            end_line=1,
            status="unparsed",
            references=[],
            issues=[],
            node=ast.Unparsed(),
            file_path=deselected.rel_path,
        ),
    ]
    inventory.replace_discovery(
        objects,
        [
            DiscoveryEdge("v_summary", "customers", "reads", False),
            DiscoveryEdge("v_summary", "missing_object", "reads", True),
            DiscoveryEdge("ignored_function", "customers", "writes", False),
        ],
        uploads,
        {
            selected.rel_path: "one\ntwo\nthree\nfour\nfive",
            deselected.rel_path: "excluded",
        },
    )
    inventory.add_issue(selected.id, severity="warning", code="selected.warning", message="private")
    inventory.add_issue(deselected.id, severity="error", code="excluded.error", message="private")

    aggregates = inventory.aggregates()
    assert aggregates == {
        "objects_by_type": {"table": 1, "view": 1},
        "parse_status_counts": {"parsed": 1, "partial": 1},
        "files_by_detected_type": {"sql:ansi": 1},
        "total_dependencies": 2,
        "dependencies_by_kind": {"reads": 2},
        "external_reference_count": 1,
        "issues_by_severity": {"warning": 1},
        "total_source_lines": 5,
    }
    initial_fingerprint = inventory.fingerprint()
    assert initial_fingerprint
    canonical = json.dumps(aggregates, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert initial_fingerprint == hashlib.sha256(canonical).hexdigest()
    assert inventory.fingerprint() == initial_fingerprint

    inventory.set_selected(selected.id, False)
    assert inventory.fingerprint() == ""
    inventory.set_selected(selected.id, True)
    assert inventory.fingerprint() == initial_fingerprint
    inventory.delete_upload(selected.id)
    assert inventory.fingerprint() == ""
    inventory.close()


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


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def _confirm_teradata(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post(
        "/api/session/confirm",
        json={"source": "teradata", "target": "snowflake"},
        headers=headers,
    )
    assert response.status_code == 200


def _wait_for_discovery(client: TestClient) -> dict:
    for _ in range(1000):
        status = client.get("/api/discovery/status", headers=HOST_HEADER).json()
        if status["state"] == "done":
            return status
        time.sleep(0.01)
    raise AssertionError(f"Discovery did not finish: {status}")


def test_reuploading_teradata_pack_replaces_previous_inventory(client: TestClient) -> None:
    csrf = launch(client)
    headers = _headers(csrf)
    _confirm_teradata(client, headers)
    pack_root = Path(__file__).resolve().parents[4] / "testpacks" / "teradata"
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(pack_root.glob("*.sql")):
            archive.write(path, path.name)
    pack = archive_bytes.getvalue()

    first = client.post(
        "/api/uploads",
        files=[("files", ("teradata-pack.zip", pack, "application/zip"))],
        headers=headers,
    )
    assert first.status_code == 200
    _wait_for_discovery(client)
    first_uploads = client.get("/api/uploads", headers=HOST_HEADER).json()
    first_ids = {row["id"] for row in first_uploads}
    first_total = client.get("/api/inventory?page_size=300", headers=HOST_HEADER).json()["total"]
    assert first_uploads

    second = client.post(
        "/api/uploads",
        files=[("files", ("teradata-pack.zip", pack, "application/zip"))],
        headers=headers,
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert first_ids.issubset(set(second_payload["replaced"]))
    _wait_for_discovery(client)

    uploads = client.get("/api/uploads", headers=HOST_HEADER).json()
    page = client.get("/api/inventory?page_size=300", headers=HOST_HEADER).json()
    assert len(uploads) == len(first_uploads)
    assert page["total"] == first_total
    for item in page["items"]:
        detail = client.get(f"/api/inventory/{item['id']}", headers=HOST_HEADER).json()
        assert not any(issue["code"] == "ambiguous_reference" for issue in detail["issues"])


def test_rejected_duplicate_zip_entry_keeps_existing_copy(client: TestClient) -> None:
    csrf = launch(client)
    headers = _headers(csrf)
    _confirm_teradata(client, headers)
    good = client.post(
        "/api/uploads",
        files=[
            (
                "files",
                (
                    "same-pack.zip",
                    _zip_bytes({"tables.sql": b"CREATE TABLE sales.kept_table(id INTEGER);"}),
                    "application/zip",
                ),
            )
        ],
        headers=headers,
    )
    assert good.status_code == 200
    good_id = good.json()["uploads"][0]["id"]
    _wait_for_discovery(client)

    rejected = client.post(
        "/api/uploads",
        files=[
            (
                "files",
                ("same-pack.zip", _zip_bytes({"tables.sql": b"x" * 50_000}), "application/zip"),
            )
        ],
        headers=headers,
    )
    assert rejected.status_code == 200
    assert rejected.json()["uploads"][0]["status"] == "rejected"
    assert rejected.json()["replaced"] == []
    upload_ids = {row["id"] for row in client.get("/api/uploads", headers=HOST_HEADER).json()}
    assert good_id in upload_ids
    items = client.get("/api/inventory?page_size=300", headers=HOST_HEADER).json()["items"]
    assert any(item["qualified_name"] == "sales.kept_table" for item in items)


def test_last_accepted_duplicate_path_in_request_wins(client: TestClient) -> None:
    csrf = launch(client)
    headers = _headers(csrf)
    _confirm_teradata(client, headers)
    single = client.post(
        "/api/uploads",
        files=[
            (
                "files",
                ("same.sql", b"CREATE TABLE sales.baseline_table(id INTEGER);", "text/plain"),
            )
        ],
        headers=headers,
    )
    assert single.status_code == 200
    single_upload_id = single.json()["uploads"][0]["id"]
    _wait_for_discovery(client)
    single_total = client.get("/api/inventory", headers=HOST_HEADER).json()["total"]
    assert single_total == 1
    assert client.delete(f"/api/uploads/{single_upload_id}", headers=headers).json() == {
        "deleted": True
    }

    response = client.post(
        "/api/uploads",
        files=[
            ("files", ("same.sql", b"CREATE TABLE sales.first_table(id INTEGER);", "text/plain")),
            ("files", ("same.sql", b"CREATE TABLE sales.last_table(id INTEGER);", "text/plain")),
        ],
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["uploads"]) == 1
    assert payload["replaced"] == []
    _wait_for_discovery(client)
    uploads = client.get("/api/uploads", headers=HOST_HEADER).json()
    assert len(uploads) == 1
    session = client.app.state.current_session
    matching_session_rows = [
        row for row in session["uploads"].values() if row.rel_path == "same.sql"
    ]
    assert len(matching_session_rows) == 1
    items = client.get("/api/inventory?page_size=300", headers=HOST_HEADER).json()["items"]
    names = {item["qualified_name"] for item in items}
    assert len(items) == single_total
    assert "sales.last_table" in names
    assert "sales.first_table" not in names
    assert "sales.baseline_table" not in names
