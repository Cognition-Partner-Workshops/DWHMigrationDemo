from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from pathlib import Path

from migraai.agents.job import ConversionJob
from migraai.core.audit import AuditLog
from migraai.core.crypto import EncryptedFileStore, SessionCipher
from migraai.devin.attachment import render_batch
from migraai.devin.client import build_devin_client
from migraai.devin.replay import ReplayDevinClient
from migraai.discovery.detect import decode_source
from migraai.discovery.inventory import Inventory
from migraai.discovery.pipeline import discover_files
from migraai.discovery.uploads import UploadRow
from migraai.platforms.pack import load_pack

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = ROOT / "backend/tests/fixtures/replay/teradata-snowflake/run1"
_UNSAFE = re.compile(
    r"(?i)(?:demo-secret|\b(?:apk|dsk)[_-]?[A-Za-z0-9_-]{12,}\b|"
    r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}\b|\bAKIA[0-9A-Z]{16}\b)"
)
_PASSWORD = re.compile(r"(?i)\b(?:password|passwd|pwd)\b\s*[:=]\s*[\"']?([^\s,;\"']+)")
_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9])/(?:home|Users)/[^\s\"']+|"
    r"/tmp/[A-Za-z0-9._/-]+|(?i:\b[A-Z]:\\[^\s\"']+)"
)


def test_replay_client_matches_names_and_role_and_rewrites_ids(tmp_path, monkeypatch) -> None:
    (tmp_path / "voter_a.json").write_text(
        json.dumps(
            {
                "objects": [
                    {
                        "object_id": "run1-id",
                        "qualified_name": "sales.table_a",
                        "type": "table",
                        "files": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "voter_b.json").write_text(
        json.dumps({"objects": []}),
        encoding="utf-8",
    )
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "batches": [
                    {
                        "object_ids": ["run1-id"],
                        "outputs": {
                            "voter_a": "voter_a.json",
                            "voter_b": "voter_b.json",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIGRAAI_TEST_REPLAY_DEVIN", str(tmp_path))
    monkeypatch.setenv("MIGRAAI_TEST_MOCK_DEVIN", "1")

    client = build_devin_client("https://invalid.example", "", "")
    assert isinstance(client, ReplayDevinClient)
    attachment = client.upload_attachment(
        "batch.json",
        render_batch(
            [
                {
                    "object_id": "fresh-id",
                    "source_qualified_name": "sales.table_a",
                    "object_type": "table",
                }
            ]
        ),
    )
    session_id, url = client.create_session(
        f'ATTACHMENT:"{attachment}"',
        schema={},
        max_acu_limit=None,
        title="test",
        tags=["voter-A"],
    )
    session = client.get_session(session_id)

    assert url.startswith("replay://run1/voter_a/")
    assert session["structured_output"] == {"objects": [{"object_id": "fresh-id", "files": []}]}
    assert session["acus"] is None
    assert session["replayed"] is True


def test_replay_fixtures_are_sanitized_and_have_deterministic_sessions() -> None:
    index = json.loads((FIXTURE_DIR / "index.json").read_text(encoding="utf-8"))
    files = [
        FIXTURE_DIR / batch_file
        for batch in index["batches"]
        for batch_file in batch["outputs"].values()
    ]
    documents = [index] + [json.loads(path.read_text(encoding="utf-8")) for path in files]

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)

    for document in documents:
        for text in strings(document):
            assert _UNSAFE.search(text) is None
            assert not any(
                match.group(1).rstrip(".,;:)") != "***MASKED***"
                for match in _PASSWORD.finditer(text)
            )
            assert all(
                match.rstrip(".,;):") == "/tmp/customer.csv"
                for match in _ABSOLUTE_PATH.findall(text)
            )
    for session in index["sessions"]:
        assert session["session_id"].startswith("replay-run1-")
        assert session["url"].startswith("replay://run1/")
        assert session["acus"] is None


def _teradata_session(tmp_path: Path) -> dict:
    pack_dir = ROOT / "testpacks/teradata"
    files = {
        path.relative_to(pack_dir).as_posix(): path.read_bytes()
        for path in pack_dir.rglob("*.sql")
        if path.is_file()
    }
    files = {f"teradata-testpack.zip/{name}": data for name, data in files.items()}
    inventory = Inventory()
    store = EncryptedFileStore(tmp_path / "store", SessionCipher())
    uploads = {}
    previews = {}
    for rel_path, data in files.items():
        row = UploadRow(
            id=uuid.uuid4().hex,
            rel_path=rel_path,
            size=len(data),
            detected_type="sql",
            status="done",
            store_path=f"uploads/{rel_path}",
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


def test_run1_replays_over_the_teradata_pack(tmp_path) -> None:
    session = _teradata_session(tmp_path)
    job = ConversionJob(
        session=session,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        timeout_minutes=1,
        client_factory=lambda _role: ReplayDevinClient(str(FIXTURE_DIR)),
        pack=load_pack("snowflake"),
    )
    job.start()
    job.join(timeout=300)

    assert not job.is_alive()
    assert job.state == "done"
    assert len(job.results) == 56
    counts = Counter(result["status"] for result in job.results.values())
    print("run1 replay status counts:", dict(counts))
    objects = {str(item["id"]): item for item in job.all_objects}
    ordered_results = sorted(
        job.results.items(),
        key=lambda pair: objects[pair[0]]["qualified_name"],
    )
    for object_id, result in ordered_results:
        print(
            "run1 replay object:",
            objects[object_id]["qualified_name"],
            result["status"],
            result.get("agreement"),
        )
