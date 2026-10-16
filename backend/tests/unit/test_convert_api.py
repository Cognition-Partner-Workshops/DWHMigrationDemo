from __future__ import annotations

import io
import json
import threading
import uuid
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

import migraai.api.convert as convert_api
import migraai.devin.client as devin_client
from migraai.core.crypto import EncryptedFileStore, SessionCipher
from migraai.core.settings_store import SettingsUpdate
from migraai.devin.mock import MockDevinClient
from migraai.discovery.detect import decode_source
from migraai.discovery.inventory import Inventory
from migraai.discovery.pipeline import discover_files
from migraai.discovery.uploads import UploadRow
from migraai.packaging.parts import collect_parts
from migraai.packaging.snapshot import build_package_input
from migraai.packaging.zipper import build_zip

HOST_HEADER = {"Host": "127.0.0.1:80"}
SOURCE_CODE = "CREATE TABLE sales.orders (order_id INTEGER, private_marker_9472 INTEGER);"
REPLAY_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/replay/teradata-snowflake/run1"
TERADATA_PACK = Path(__file__).resolve().parents[3] / "testpacks" / "teradata"


def _csrf_headers(csrf: str) -> dict[str, str]:
    return {**HOST_HEADER, "X-MigraAI-CSRF": csrf}


def _launch(client: TestClient) -> str:
    response = client.get(
        "/api/auth/launch",
        params={"token": "test-launch-token"},
        headers=HOST_HEADER,
        follow_redirects=False,
    )
    assert response.status_code == 303
    return str(client.cookies["migraai_csrf"])


def _add_conversion_session(app, data_dir: Path) -> str:
    session_id = uuid.uuid4().hex
    session_dir = data_dir / "sessions" / session_id
    store = EncryptedFileStore(session_dir, SessionCipher())
    inventory = Inventory()
    rel_path = "objects.sql"
    source = SOURCE_CODE.encode()
    upload = UploadRow(
        id=uuid.uuid4().hex,
        rel_path=rel_path,
        size=len(source),
        detected_type="sql",
        status="done",
        object_count=1,
        store_path=f"uploads/{uuid.uuid4().hex}",
    )
    store.write(upload.store_path, source)
    inventory.add_upload(upload)
    files = {rel_path: SOURCE_CODE}
    objects, edges = discover_files({rel_path: source}, "teradata")
    inventory.replace_discovery(objects, edges, {upload.id: upload}, files)
    app.state.current_session = {
        "session_id": session_id,
        "source": "teradata",
        "target": "snowflake",
        "inventory": inventory,
        "store": store,
        "uploads": {upload.id: upload},
    }
    return str(inventory.page(page_size=10)["items"][0]["id"])


def _add_teradata_pack_session(app, data_dir: Path) -> list[dict]:
    session_id = uuid.uuid4().hex
    store = EncryptedFileStore(data_dir / "sessions" / session_id, SessionCipher())
    inventory = Inventory()
    files = {
        path.relative_to(TERADATA_PACK).as_posix(): path.read_bytes()
        for path in sorted(TERADATA_PACK.rglob("*.sql"))
        if path.is_file()
    }
    uploads: dict[str, UploadRow] = {}
    previews: dict[str, str] = {}
    for rel_path, content in files.items():
        upload = UploadRow(
            id=uuid.uuid4().hex,
            rel_path=rel_path,
            size=len(content),
            detected_type="sql",
            status="done",
            store_path=f"uploads/{uuid.uuid4().hex}",
        )
        store.write(upload.store_path, content)
        inventory.add_upload(upload)
        uploads[upload.id] = upload
        previews[rel_path] = decode_source(content)[0]
    objects, edges = discover_files(files, "teradata")
    inventory.replace_discovery(objects, edges, uploads, previews)
    app.state.current_session = {
        "session_id": session_id,
        "source": "teradata",
        "target": "snowflake",
        "inventory": inventory,
        "store": store,
        "uploads": uploads,
    }
    return inventory.page(page_size=300)["items"]


def test_estimate_requires_pair_and_selected_objects(
    client: TestClient, app, data_dir: Path
) -> None:
    csrf = _launch(client)
    headers = _csrf_headers(csrf)
    assert client.post("/api/convert/estimate", json={}, headers=headers).status_code == 409

    _add_conversion_session(app, data_dir)
    session = app.state.current_session
    session["uploads"][next(iter(session["uploads"]))].selected = False
    session["inventory"].set_selected(next(iter(session["uploads"])), False)
    response = client.post("/api/convert/estimate", json={}, headers=headers)
    assert response.status_code == 409
    assert "selected parsed objects" in response.json()["detail"]


def test_selected_estimate_and_validation(client: TestClient, app, data_dir: Path) -> None:
    csrf = _launch(client)
    object_id = _add_conversion_session(app, data_dir)
    headers = _csrf_headers(csrf)

    estimate = client.post(
        "/api/convert/estimate",
        params=[("object_ids", object_id)],
        json={},
        headers=headers,
    )
    assert estimate.status_code == 200
    assert estimate.json()["objects"] == 1
    assert estimate.json()["batches"] == 1

    for object_ids in ([], [object_id, object_id], ["unknown-object"]):
        response = client.post(
            "/api/convert",
            json={"consent": True, "object_ids": object_ids},
            headers=headers,
        )
        assert response.status_code == 400

    no_base = client.post(
        "/api/convert",
        json={"consent": True, "object_ids": [object_id]},
        headers=headers,
    )
    assert no_base.status_code == 409
    assert no_base.json()["detail"] == (
        "Convert selected objects needs a finished conversion to merge with."
    )


def test_selected_conversion_uses_finished_job_as_merge_base(
    client: TestClient,
    app,
    data_dir: Path,
    monkeypatch,
) -> None:
    csrf = _launch(client)
    object_id = _add_conversion_session(app, data_dir)
    monkeypatch.setenv("MIGRAAI_TEST_MOCK_DEVIN", "1")
    headers = _csrf_headers(csrf)

    first = client.post("/api/convert", json={"consent": True}, headers=headers)
    assert first.status_code == 200
    base = app.state.convert_job
    base.join(timeout=30)
    assert not base.is_alive()

    second = client.post(
        "/api/convert",
        json={"consent": True, "object_ids": [object_id]},
        headers=headers,
    )
    assert second.status_code == 200
    job = app.state.convert_job
    job.join(timeout=30)
    assert not job.is_alive()
    assert job.base is base
    assert job.base_run_id == first.json()["job_id"]
    assert job.reconverted_object_ids == (object_id,)
    job.kept_base_object_ids[object_id] = "cancelled"
    status = client.get(f"/api/convert/jobs/{job.run_id}", headers=headers)
    assert status.status_code == 200
    assert status.json()["kept_base_object_ids"] == {object_id: "cancelled"}

    audit_events = [
        json.loads(line)
        for line in app.state.audit_log.path.read_text(encoding="utf-8").splitlines()
    ]
    consent = next(
        event
        for event in audit_events
        if event["action"] == "convert.consent" and event["target"] == job.run_id
    )
    assert consent["details"]["object_count"] == 1
    assert consent["details"]["base_run_id"] == base.run_id


def test_cancel_selected_reconversion_finalises_results_before_api_returns(
    client: TestClient,
    app,
    data_dir: Path,
    monkeypatch,
) -> None:
    csrf = _launch(client)
    headers = _csrf_headers(csrf)
    objects = _add_teradata_pack_session(app, data_dir)
    convertible_objects = [item for item in objects if not item.get("external")]
    assert len(convertible_objects) == 56
    selected_ids = [str(item["id"]) for item in convertible_objects if item["type"] == "grant"][:3]
    assert len(selected_ids) == 3
    monkeypatch.setenv("MIGRAAI_TEST_MOCK_DEVIN", "1")

    original_factory = convert_api.build_conversion_client_factory
    factory_calls = 0
    entered = threading.Event()
    release = threading.Event()

    class GatedMockClient(MockDevinClient):
        def get_session(self, session_id: str) -> dict:
            entered.set()
            if not release.wait(timeout=15):
                raise TimeoutError("Mock session gate timed out.")
            return super().get_session(session_id)

    def build_factory(base_url: str, org_id: str, pat: str):
        nonlocal factory_calls
        factory_calls += 1
        factory, replayed, host = original_factory(base_url, org_id, pat)
        if factory_calls == 3:
            return lambda _role: GatedMockClient(), replayed, host
        return factory, replayed, host

    monkeypatch.setattr(convert_api, "build_conversion_client_factory", build_factory)

    first = client.post("/api/convert", json={"consent": True}, headers=headers)
    assert first.status_code == 200
    base = app.state.convert_job
    base.join(timeout=60)
    assert not base.is_alive()
    assert base.counts["converted"] == 56

    second = client.post(
        "/api/convert",
        json={"consent": True, "object_ids": selected_ids},
        headers=headers,
    )
    assert second.status_code == 200
    merged = app.state.convert_job
    merged.join(timeout=60)
    assert not merged.is_alive()
    assert merged.counts == base.counts

    third = client.post(
        "/api/convert",
        json={"consent": True, "object_ids": selected_ids},
        headers=headers,
    )
    assert third.status_code == 200
    job = app.state.convert_job
    try:
        assert entered.wait(timeout=10)
        expected_counts = dict(merged.counts)

        cancelled = client.post(
            f"/api/convert/jobs/{job.run_id}/cancel",
            headers=headers,
        )
        assert cancelled.status_code == 200
        expected_kept = dict.fromkeys(selected_ids, "cancelled")
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["counts"] == expected_counts
        assert cancelled.json()["counts"]["converted"] == 56
        assert cancelled.json()["kept_base_object_ids"] == expected_kept
        assert all(
            job.results[object_id] == merged.results[object_id] for object_id in selected_ids
        )
        package = build_package_input(job, app.state.current_session, "test")
        assert len(package.included()) == 56
        archive_bytes = build_zip(package, collect_parts(package))
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            manifest_path = next(
                name for name in archive.namelist() if name.endswith("/manifest.json")
            )
            manifest = json.loads(archive.read(manifest_path))
        assert manifest["kept_base_object_ids"] == expected_kept
        assert all(
            state != "queued"
            for batch in cancelled.json()["batches"]
            for state in batch["voters"].values()
        )
        assert job.is_alive()

        status = client.get(f"/api/convert/jobs/{job.run_id}", headers=headers)
        assert status.status_code == 200
        assert status.json()["state"] == "cancelled"
        assert status.json()["counts"] == expected_counts
        assert status.json()["kept_base_object_ids"] == expected_kept

        audit_events = [
            json.loads(line)
            for line in app.state.audit_log.path.read_text(encoding="utf-8").splitlines()
        ]
        cancel_event = next(
            event
            for event in audit_events
            if event["action"] == "convert.cancel" and event["target"] == job.run_id
        )
        assert cancel_event["details"]["completed_count"] == 56
        with app.state.current_session["inventory"].lock:
            persisted_state = (
                app.state.current_session["inventory"]
                .connection.execute(
                    "SELECT state FROM conversion_run WHERE id=?",
                    (job.run_id,),
                )
                .fetchone()["state"]
            )
        assert persisted_state == "cancelled"

        release.set()
        job.join(timeout=60)
        assert not job.is_alive()
        assert job.counts == expected_counts
        assert job.kept_base_object_ids == expected_kept
    finally:
        release.set()
        job.join(timeout=60)


def test_convert_requires_explicit_consent(client: TestClient, app, data_dir: Path) -> None:
    csrf = _launch(client)
    _add_conversion_session(app, data_dir)

    response = client.post(
        "/api/convert",
        json={"consent": False},
        headers=_csrf_headers(csrf),
    )

    assert response.status_code == 400


def test_convert_returns_exact_not_configured_response(
    client: TestClient,
    app,
    data_dir: Path,
    monkeypatch,
) -> None:
    csrf = _launch(client)
    _add_conversion_session(app, data_dir)
    monkeypatch.delenv("MIGRAAI_TEST_MOCK_DEVIN", raising=False)

    response = client.post(
        "/api/convert",
        json={"consent": True},
        headers=_csrf_headers(csrf),
    )

    assert response.status_code == 412
    assert response.json() == {
        "code": "devin_not_configured",
        "message": (
            "Devin isn't configured. Add a Devin API key in Settings to convert; nothing was sent."
        ),
    }
    assert app.state.convert_job is None


def test_mock_conversion_run_status_results_detail_and_audit(
    client: TestClient,
    app,
    data_dir: Path,
    monkeypatch,
) -> None:
    csrf = _launch(client)
    object_id = _add_conversion_session(app, data_dir)
    monkeypatch.setenv("MIGRAAI_TEST_MOCK_DEVIN", "1")
    headers = _csrf_headers(csrf)

    estimate = client.post("/api/convert/estimate", json={}, headers=headers)
    assert estimate.status_code == 200
    assert estimate.json() == {
        "objects": 1,
        "batches": 1,
        "oversized_batches": 0,
        "voter_sessions": 3,
        "max_escalation_sessions": 1,
        "max_sessions": 4,
        "batch_size": 20,
        "session_timeout_minutes": 45,
        "max_concurrent_sessions": 4,
        "devin_configured": True,
        "devin_host": "local mock",
        "acu_cap": None,
    }
    started = client.post("/api/convert", json={"consent": True}, headers=headers)
    assert started.status_code == 200
    job = app.state.convert_job
    job.join(timeout=30)
    assert not job.is_alive()

    status = client.get(f"/api/convert/jobs/{started.json()['job_id']}", headers=HOST_HEADER)
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "done"
    assert status.json()["batches"][0]["voters"]["A"] == "done"
    assert (
        sum(
            status.json()["batches"][0]["counts"][key]
            for key in ("converted", "expert_review", "failed")
        )
        == 1
    )

    listed = client.get("/api/conversions", params={"search": "orders"}, headers=HOST_HEADER)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    row = listed.json()["items"][0]
    assert row["object_id"] == object_id
    assert row["checks"]["syntax"] in {"passed", "failed", "not_applicable"}

    detail = client.get(f"/api/conversions/{object_id}", headers=HOST_HEADER)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["final"]["files"]
    assert set(payload["candidates"]) == {"A", "B", "C"}
    assert payload["candidates"]["A"]["files"]
    assert payload["pairwise"]
    assert payload["checks"]
    assert len(payload["devin_sessions"]) >= 3

    events = client.get(f"/api/convert/jobs/{started.json()['job_id']}/events", headers=HOST_HEADER)
    assert events.status_code == 200
    assert "event: convert.start" in events.text
    assert "event: convert.complete" in events.text
    audit_text = app.state.audit_log.path.read_text(encoding="utf-8")
    assert "convert.consent" in audit_text
    assert "private_marker_9472" not in audit_text


def test_replay_then_live_estimate_and_consent_audit_use_the_selected_client(
    client: TestClient,
    app,
    data_dir: Path,
    monkeypatch,
) -> None:
    csrf = _launch(client)
    _add_conversion_session(app, data_dir)
    app.state.settings_store.update(SettingsUpdate(devin_pat="apk_test_token"))
    monkeypatch.setenv("MIGRAAI_TEST_REPLAY_DEVIN", str(REPLAY_FIXTURE))
    monkeypatch.setenv("MIGRAAI_TEST_REPLAY_THEN_LIVE", "1")
    monkeypatch.delenv("MIGRAAI_TEST_MOCK_DEVIN", raising=False)
    monkeypatch.setattr(devin_client, "_REPLAY_THEN_LIVE_USED", False)
    monkeypatch.setattr(
        devin_client, "build_devin_client", lambda *_args, **_kwargs: MockDevinClient()
    )
    headers = _csrf_headers(csrf)

    first_estimate = client.post("/api/convert/estimate", json={}, headers=headers)
    assert first_estimate.json()["devin_host"] == "local replay"
    first = client.post("/api/convert", json={"consent": True}, headers=headers)
    assert first.status_code == 200
    first_job = app.state.convert_job
    first_job.join(timeout=30)
    assert not first_job.is_alive()

    second_estimate = client.post("/api/convert/estimate", json={}, headers=headers)
    assert second_estimate.json()["devin_host"] == "api.devin.ai"
    second = client.post("/api/convert", json={"consent": True}, headers=headers)
    assert second.status_code == 200
    second_job = app.state.convert_job
    second_job.join(timeout=30)
    assert not second_job.is_alive()

    events = [
        json.loads(line)
        for line in app.state.audit_log.path.read_text(encoding="utf-8").splitlines()
    ]
    consent_hosts = {
        event["target"]: event["details"]["devin_host"]
        for event in events
        if event["action"] == "convert.consent"
    }
    assert consent_hosts[first.json()["job_id"]] == "local replay"
    assert consent_hosts[second.json()["job_id"]] == "api.devin.ai"


def test_cancel_endpoint_marks_active_job_cancelled(client: TestClient, app) -> None:
    csrf = _launch(client)

    class ActiveJob:
        run_id = "cancel-test"

        def __init__(self) -> None:
            self.cancelled = False

        def is_alive(self) -> bool:
            return True

        def cancel(self) -> None:
            self.cancelled = True

        def snapshot(self) -> dict:
            return {
                "run_id": self.run_id,
                "state": "cancelled",
                "progress": {"elapsed_s": 0, "batches": {}},
                "counts": {},
                "object_count": 0,
                "batch_count": 0,
            }

    job = ActiveJob()
    app.state.convert_job = job
    response = client.post(
        "/api/convert/jobs/cancel-test/cancel",
        headers=_csrf_headers(csrf),
    )
    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert job.cancelled
