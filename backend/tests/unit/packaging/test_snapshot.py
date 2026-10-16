from __future__ import annotations

import uuid
from pathlib import Path

from migraai.agents.job import ConversionJob
from migraai.core.audit import AuditLog
from migraai.core.crypto import EncryptedFileStore, SessionCipher
from migraai.devin.mock import MockDevinClient, MockScenario
from migraai.discovery.detect import decode_source
from migraai.discovery.inventory import Inventory
from migraai.discovery.pipeline import discover_files
from migraai.discovery.uploads import UploadRow
from migraai.packaging.snapshot import build_package_input
from migraai.platforms.pack import load_pack

ROOT = Path(__file__).resolve().parents[4]
TERADATA_TESTPACK = ROOT / "testpacks" / "teradata"


def _session(directory: Path, files: dict[str, bytes]) -> dict:
    inventory = Inventory()
    store = EncryptedFileStore(directory / "store", SessionCipher())
    uploads = {}
    previews = {}
    for rel_path, data in files.items():
        row = UploadRow(
            id=uuid.uuid4().hex,
            rel_path=rel_path,
            size=len(data),
            detected_type=Path(rel_path).suffix.lstrip("."),
            status="done",
            store_path=f"uploads/{uuid.uuid4().hex}",
        )
        store.write(row.store_path, data)
        inventory.add_upload(row)
        uploads[row.id] = row
        previews[rel_path] = decode_source(data)[0]
    objects, edges = discover_files(files, "teradata")
    inventory.replace_discovery(objects, edges, uploads, previews)
    return {
        "session_id": uuid.uuid4().hex,
        "source": "teradata",
        "target": "snowflake",
        "inventory": inventory,
        "store": store,
        "uploads": uploads,
    }


def _run(session: dict, directory: Path, scenario: MockScenario | None = None) -> ConversionJob:
    job = ConversionJob(
        session=session,
        audit=AuditLog(directory / "audit.jsonl"),
        batch_size=20,
        timeout_minutes=2,
        max_concurrent_sessions=4,
        client_factory=lambda _: MockDevinClient(scenario),
        pack=load_pack("snowflake"),
        devin_host="local mock",
    )
    job.start()
    job.join(timeout=180)
    assert not job.is_alive()
    return job


def test_teradata_snowflake_mock_snapshot_has_56_included_objects(tmp_path: Path) -> None:
    files = {
        path.relative_to(TERADATA_TESTPACK).as_posix(): path.read_bytes()
        for path in TERADATA_TESTPACK.rglob("*.sql")
    }
    session = _session(tmp_path / "teradata", files)
    job = _run(session, tmp_path / "teradata")

    package = build_package_input(job, session, "")

    assert package.source_platform == "teradata"
    assert package.target_platform == "snowflake"
    assert package.run_id == job.run_id
    assert len(package.included()) == 56
    assert len(package.objects) == 56
    assert package.egress is not None
    assert package.egress.objects_sent == 56
    assert package.egress.devin_host == "local mock"
    assert package.objects[0].object_id == str(job.batches[0].objects[0]["id"])


def test_failed_object_source_text_is_not_in_snapshot(tmp_path: Path) -> None:
    source = b"CREATE TABLE sales.failed_object (id INTEGER); -- source_text_marker_83726\n"
    session = _session(tmp_path / "failed", {"failed.sql": source})
    scenario = MockScenario(fail_voters={"A", "B", "C", "default"})
    job = _run(session, tmp_path / "failed", scenario)

    package = build_package_input(job, session, "")

    assert len(package.objects) == 1
    assert package.objects[0].status == "failed"
    assert package.objects[0].files == ()
    assert "source_text_marker_83726" not in repr(package)
