from __future__ import annotations

import json
import uuid
import zipfile
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from migraai.core.crypto import EncryptedFileStore, SessionCipher
from migraai.discovery.inventory import Inventory
from migraai.discovery.pipeline import discover_files
from migraai.discovery.uploads import UploadRow
from migraai.packaging.sample import sample_package

HOST_HEADER = {"Host": "127.0.0.1:80"}
SOURCE_TEXT = "CREATE TABLE sales.orders (id INTEGER); -- download_private_marker_937\n"


def _launch(client: TestClient) -> None:
    response = client.get(
        "/api/auth/launch",
        params={"token": "test-launch-token"},
        headers=HOST_HEADER,
        follow_redirects=False,
    )
    assert response.status_code == 303


def _add_session(
    app,
    data_dir: Path,
    rel_path: str = "orders.sql",
    archive_id: str | None = None,
) -> None:
    session_id = uuid.uuid4().hex
    store = EncryptedFileStore(data_dir / "sessions" / session_id, SessionCipher())
    inventory = Inventory()
    source = SOURCE_TEXT.encode()
    upload = UploadRow(
        id=uuid.uuid4().hex,
        rel_path=rel_path,
        size=len(source),
        detected_type=Path(rel_path).suffix.lstrip("."),
        status="done",
        object_count=1,
        store_path=f"uploads/{uuid.uuid4().hex}",
        archive_id=archive_id,
    )
    store.write(upload.store_path, source)
    inventory.add_upload(upload)
    objects, edges = discover_files({rel_path: source}, "teradata")
    inventory.replace_discovery(
        objects,
        edges,
        {upload.id: upload},
        {rel_path: SOURCE_TEXT},
    )
    app.state.current_session = {
        "session_id": session_id,
        "source": "teradata",
        "target": "snowflake",
        "inventory": inventory,
        "store": store,
        "uploads": {upload.id: upload},
    }


def test_download_returns_409_without_a_conversion_job(
    client: TestClient, app, data_dir: Path
) -> None:
    _launch(client)
    _add_session(app, data_dir)

    response = client.get("/api/download", headers=HOST_HEADER)

    assert response.status_code == 409
    assert "No conversion job" in response.json()["detail"]


def test_download_returns_409_for_an_active_job(client: TestClient, app, data_dir: Path) -> None:
    _launch(client)
    _add_session(app, data_dir)
    app.state.convert_job = SimpleNamespace(state="running")

    response = client.get("/api/download", headers=HOST_HEADER)

    assert response.status_code == 409
    assert "wait until it is done or cancelled" in response.json()["detail"]


def test_download_returns_409_when_no_object_is_included(
    client: TestClient,
    app,
    data_dir: Path,
    monkeypatch,
) -> None:
    from migraai.api import download as download_api

    _launch(client)
    _add_session(app, data_dir)
    app.state.convert_job = SimpleNamespace(state="done")
    base = sample_package()
    failed_only = replace(base, objects=(base.objects[-1],))
    monkeypatch.setattr(download_api, "build_package_input", lambda *_: failed_only)

    response = client.get("/api/download", headers=HOST_HEADER)

    assert response.status_code == 409
    assert "no converted or expert-review objects" in response.json()["detail"]


def test_download_zip_name_and_audit_event_contain_counts_not_code(
    client: TestClient,
    app,
    data_dir: Path,
    monkeypatch,
) -> None:
    _launch(client)
    _add_session(app, data_dir)
    monkeypatch.setenv("MIGRAAI_TEST_MOCK_DEVIN", "1")
    started = client.post(
        "/api/convert",
        json={"consent": True},
        headers={**HOST_HEADER, "X-MigraAI-CSRF": client.cookies["migraai_csrf"]},
    )
    assert started.status_code == 200, started.text
    job = app.state.convert_job
    job.join(timeout=60)
    assert not job.is_alive()

    response = client.get(
        "/api/download",
        params={"name": "release package"},
        headers=HOST_HEADER,
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["content-disposition"] == (
        'attachment; filename="release-package-converted.zip"'
    )
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert "release-package-converted/manifest.json" in archive.namelist()

    audit_lines = app.state.audit_log.path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in audit_lines]
    event = next(record for record in events if record["action"] == "download.package")
    assert set(event["details"]) == {"included", "excluded", "converted", "expert_review"}
    assert all(type(value) is int for value in event["details"].values())
    assert event["details"]["included"] == 1
    assert event["details"]["excluded"] == 0
    assert "download_private_marker_937" not in json.dumps(event)


def test_download_archive_member_uses_archive_stem(
    client: TestClient,
    app,
    data_dir: Path,
    monkeypatch,
) -> None:
    _launch(client)
    _add_session(
        app,
        data_dir,
        "teradata-testpack.zip/tables.sql",
        archive_id="test-archive",
    )
    monkeypatch.setenv("MIGRAAI_TEST_MOCK_DEVIN", "1")
    started = client.post(
        "/api/convert",
        json={"consent": True},
        headers={**HOST_HEADER, "X-MigraAI-CSRF": client.cookies["migraai_csrf"]},
    )
    assert started.status_code == 200, started.text
    job = app.state.convert_job
    job.join(timeout=60)
    assert not job.is_alive()

    response = client.get("/api/download", headers=HOST_HEADER)

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        'attachment; filename="teradata-testpack-converted.zip"'
    )


def test_report_routes_return_503_when_the_generator_is_missing(
    client: TestClient,
    monkeypatch,
) -> None:
    from migraai.api import download as download_api

    _launch(client)

    def missing_module(name: str):
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(download_api.importlib, "import_module", missing_module)
    for path in ("/api/report.md", "/api/report.pdf"):
        response = client.get(path, headers=HOST_HEADER)
        assert response.status_code == 503
        assert response.json()["detail"] == "Report generator not available yet."


def test_report_routes_call_the_available_session_report(
    client: TestClient,
    app,
    data_dir: Path,
    monkeypatch,
) -> None:
    from migraai.api import download as download_api

    _launch(client)
    _add_session(app, data_dir)
    package = sample_package()
    app.state.convert_job = SimpleNamespace(state="done")
    monkeypatch.setattr(
        download_api,
        "_report_module",
        lambda: SimpleNamespace(
            build_report_md=lambda actual: (
                "# Session report" if actual is package else "wrong package"
            ),
            build_report_pdf=lambda actual: b"%PDF-report" if actual is package else b"wrong",
        ),
    )
    monkeypatch.setattr(download_api, "build_package_input", lambda *_: package)

    markdown = client.get("/api/report.md", headers=HOST_HEADER)
    pdf = client.get("/api/report.pdf", headers=HOST_HEADER)

    assert markdown.status_code == 200
    assert markdown.text == "# Session report"
    assert markdown.headers["content-disposition"] == ('attachment; filename="session-report.md"')
    assert pdf.status_code == 200
    assert pdf.content == b"%PDF-report"
