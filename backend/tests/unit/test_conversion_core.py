from __future__ import annotations

import importlib
import os
import time
import uuid
from pathlib import Path
from types import ModuleType

import pytest

from migraai.agents.batching import Batch, plan_batches
from migraai.agents.context import build_batch
from migraai.agents.job import ConversionJob
from migraai.agents.naming import build_name_map
from migraai.core.audit import AuditLog
from migraai.core.crypto import EncryptedFileStore, SessionCipher
from migraai.devin.attachment import parse_batch, render_batch
from migraai.devin.mock import MockDevinClient, MockScenario
from migraai.discovery import ast
from migraai.discovery.detect import decode_source
from migraai.discovery.inventory import Inventory
from migraai.discovery.objects import ParsedObject
from migraai.discovery.pipeline import discover_files
from migraai.discovery.uploads import UploadRow
from migraai.platforms.pack import TargetPack, load_pack
from migraai.validation.findings import CheckResult
from migraai.voting.confidence import overall_score, score
from migraai.voting.consensus import Candidate, agreement, choose
from migraai.voting.normalise import normalise, normalise_files

ROOT = Path(__file__).resolve().parents[3]
TERADATA_PACK = ROOT / "testpacks" / "teradata"
INFORMATICA_PACK = ROOT / "testpacks" / "etl" / "informatica"
TARGETS = (
    "databricks",
    "redshift",
    "snowflake",
    "fabric",
    "bigquery",
    "synapse",
    "clickhouse",
)


def _target_pack(target: str) -> TargetPack:
    example = load_pack("_example")
    naming = example.naming.model_copy(update={"target": target})
    return TargetPack(target, example.guidance, example.rules, naming)


def _inventory_session(directory: Path, pack_dir: Path, source: str) -> dict:
    inventory = Inventory()
    store = EncryptedFileStore(directory / "store", SessionCipher())
    extensions = (
        {".sql"}
        if source == "teradata"
        else {".xml"}
        if source == "informatica"
        else {".sql", ".xml", ".py", ".json", ".yaml"}
    )
    files = {
        path.relative_to(pack_dir).as_posix(): path.read_bytes()
        for path in pack_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in extensions
    }
    uploads = {}
    previews = {}
    for rel_path, data in files.items():
        row = UploadRow(
            id=uuid.uuid4().hex,
            rel_path=rel_path,
            size=len(data),
            detected_type=Path(rel_path).suffix.lstrip("."),
            status="done",
            store_path=f"uploads/{rel_path}",
        )
        store.write(row.store_path, data)
        inventory.add_upload(row)
        uploads[row.id] = row
        previews[rel_path] = decode_source(data)[0]
    objects, edges = discover_files(files, source)
    inventory.replace_discovery(objects, edges, uploads, previews)
    return {
        "session_id": uuid.uuid4().hex,
        "source": source,
        "target": "snowflake",
        "inventory": inventory,
        "store": store,
        "uploads": uploads,
    }


def _result_files(content: str, rel_path: str = "out.sql") -> list[dict]:
    return [{"rel_path": rel_path, "language": "sql", "content": content}]


def test_batching_respects_dependencies_cycles_limits_and_external_objects() -> None:
    objects = [
        {"id": "a", "qualified_name": "s.a", "file": "a.sql", "start_line": 1, "status": "partial"},
        {
            "id": "b",
            "qualified_name": "s.b",
            "file": "b.sql",
            "start_line": 1,
            "status": "unparsed",
        },
        {"id": "c", "qualified_name": "s.c", "file": "c.sql", "start_line": 1, "status": "parsed"},
        {
            "id": "x",
            "qualified_name": "s.x",
            "file": "x.sql",
            "start_line": 1,
            "status": "parsed",
            "external": True,
        },
    ]
    edges = [
        {"source_id": "a", "target_id": "b"},
        {"source_id": "a", "target_id": "x", "external": True},
    ]
    batches = plan_batches(objects, edges, 2)
    ordered = [item["id"] for batch in batches for item in batch.objects]
    assert ordered.index("b") < ordered.index("a")
    assert "x" not in ordered
    assert [name for batch in batches for name in batch.partial_objects] == ["s.a"]
    assert [name for batch in batches for name in batch.unparsed_objects] == ["s.b"]
    assert any("s.x" in batch.external_dependencies for batch in batches)
    assert all(len(batch.objects) <= 2 or batch.oversized for batch in batches)

    cycle = [
        {"source_id": "a", "target_id": "b"},
        {"source_id": "b", "target_id": "a"},
    ]
    cycle_batches = plan_batches(objects[:2], cycle, 1)
    assert len(cycle_batches) == 1
    assert cycle_batches[0].oversized
    assert set(cycle_batches[0].object_ids) == {"a", "b"}


def test_name_map_preserves_schema_case_and_quotes_reserved_identifiers() -> None:
    pack = load_pack("_example")
    name_map = build_name_map(
        [
            {"qualified_name": "Sales.SELECT", "schema_name": "Sales", "name": "SELECT"},
            {"qualified_name": "Mixed.Name", "schema_name": "Mixed", "name": "Name"},
            {"qualified_name": "external", "external": True},
        ],
        pack,
    )
    assert name_map == {
        "Sales.SELECT": "sales.`select`",
        "Mixed.Name": "mixed.name",
    }


def test_context_masks_secrets_and_contains_only_the_requested_batch(tmp_path: Path) -> None:
    source_text = (
        "CREATE TABLE sales.account (id INTEGER); -- PASSWORD 'hidden' IDENTIFIED BY hidden_token\n"
    )
    inventory = Inventory()
    store = EncryptedFileStore(tmp_path / "source", SessionCipher())
    row = UploadRow(
        id="upload-one",
        rel_path="relative/account.sql",
        size=len(source_text.encode()),
        detected_type="sql",
        status="done",
        store_path="uploads/account.sql",
    )
    store.write(row.store_path, source_text.encode())
    inventory.add_upload(row)
    parsed = ParsedObject(
        type="table",
        schema="sales",
        name="account",
        qualified_name="sales.account",
        start_line=1,
        end_line=1,
        status="parsed",
        references=[],
        issues=[],
        node=ast.Unparsed(text=source_text, reason="test context"),
        file_path=row.rel_path,
    )
    inventory.replace_discovery([parsed], [], {row.id: row}, {row.rel_path: source_text})
    other = UploadRow(
        id="upload-two",
        rel_path="other/secret.sql",
        size=11,
        detected_type="sql",
        store_path="uploads/other.sql",
    )
    store.write(other.store_path, b"DO NOT ATTACH")
    session = {
        "inventory": inventory,
        "store": store,
        "uploads": {row.id: row, other.id: other},
        "devin_key": "do-not-attach",
        "settings": {"private": "do-not-attach"},
    }
    pack = load_pack("_example")
    objects = inventory.page(page_size=10)["items"]
    batch = Batch(index=0, objects=objects)
    attachment, converted = build_batch(
        session,
        batch,
        "teradata",
        "snowflake",
        pack,
        build_name_map(objects, pack),
    )
    rendered = parse_batch(attachment)
    assert len(rendered) == len(converted) == 1
    assert "PASSWORD '***'" in rendered[0]["source_text"]
    assert "IDENTIFIED BY ***" in rendered[0]["source_text"]
    assert "hidden" not in attachment and "hidden_token" not in attachment
    assert "DO NOT ATTACH" not in attachment
    assert str(tmp_path) not in attachment
    assert "do-not-attach" not in attachment
    assert rendered[0]["rel_path"] == "relative/account.sql"


def test_attachment_sections_do_not_change_object_round_trip() -> None:
    text = render_batch(
        [{"object_id": "one", "rel_path": "a.sql", "language": "sql", "source_text": "SELECT 1;"}],
        sections=[("Guidance", "Use native target syntax.")],
    )
    assert "## Guidance" in text
    assert parse_batch(text) == [
        {
            "object_id": "one",
            "rel_path": "a.sql",
            "language": "sql",
            "source_text": "SELECT 1;",
        }
    ]


def test_normalisation_and_consensus_ties_missing_voters() -> None:
    assert normalise("select A from T;", "sql", "snowflake") == normalise(
        "SELECT a FROM t", "sql", "snowflake"
    )
    assert normalise("x = 1\n", "python", "snowflake") == normalise("x=1", "python", "snowflake")
    assert normalise('{"b": 1, "a": 2}', "json", "snowflake") == '{"a":2,"b":1}'
    assert normalise("b: 1\na: 2\n", "yaml", "snowflake") == '{"a":2,"b":1}'
    files = [
        {"rel_path": "z.sql", "language": "sql", "content": "SELECT 2"},
        {"rel_path": "a.sql", "language": "sql", "content": "SELECT 1"},
    ]
    assert normalise_files(files, "snowflake").startswith("a.sql")
    assert agreement("select x", "select x") == 1.0

    candidates = {
        "A": Candidate("A", "id", _result_files("SELECT 1"), normalized="same"),
        "B": Candidate("B", "id", _result_files("SELECT 1"), normalized="same"),
        "C": Candidate("C", "id", _result_files("SELECT 2"), normalized="different"),
    }
    checks = {
        "A": [CheckResult("syntax", "passed", [])],
        "B": [CheckResult("syntax", "passed", []), CheckResult("lint", "passed", [])],
    }
    picked = choose(candidates, checks)
    assert picked.picked_voter == "B"
    assert len(picked.pairwise) == 3
    missing = choose({"A": candidates["A"], "B": None, "C": None})
    assert missing.picked_voter == "A"
    assert missing.agreement == 0.0


def test_confidence_uses_weighted_object_counts() -> None:
    assert score(0.5, 0.75, [0.4, 0.6]) == pytest.approx(0.6)
    assert score(1.0, 1.0, []) == pytest.approx(0.8)
    assert overall_score([0.5, 0.9], [1, 3]) == pytest.approx(0.8)


def test_langsmith_tracing_is_forced_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    import migraai.agents.workflow as workflow

    importlib.reload(workflow)
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


@pytest.mark.parametrize("target", TARGETS)
def test_teradata_conversion_end_to_end_each_target(
    target: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = _inventory_session(tmp_path / target, TERADATA_PACK, "teradata")
    session["target"] = target
    pack = _target_pack(target)
    clients = []
    scenario = MockScenario(different_candidate_per_stance=True)

    def client_factory(voter: str) -> MockDevinClient:
        client = MockDevinClient(scenario)
        clients.append(client)
        return client

    job = ConversionJob(
        session=session,
        audit=AuditLog(tmp_path / target / "audit.jsonl"),
        batch_size=20,
        timeout_minutes=2,
        max_concurrent_sessions=4,
        confidence_threshold=1.0,
        client_factory=client_factory,
        pack=pack,
    )
    batch_count = len(job.batches)
    assert job.snapshot()["object_count"] == 56
    assert batch_count == 3
    job.start()
    job.join(timeout=180)
    assert not job.is_alive()
    snapshot = job.snapshot()
    assert snapshot["state"] == "done"
    assert len(job.results) == snapshot["object_count"]
    assert (
        snapshot["counts"]["converted"] + snapshot["counts"]["expert_review"]
        == snapshot["object_count"]
    )
    assert snapshot["counts"]["escalated"] == snapshot["object_count"]
    assert all(batch["escalated"] for batch in snapshot["progress"]["batches"].values())
    session_count = sum(len(client.sessions) for client in clients)
    assert session_count == 12, (
        f"progress={snapshot['progress']['batches']} "
        f"clients={[len(client.sessions) for client in clients]}"
    )
    assert "CREATE MACRO sales.refresh_daily" not in caplog.text
    with session["inventory"].lock:
        persisted = (
            session["inventory"]
            .connection.execute(
                "SELECT count(*) FROM conversion_object WHERE run_id=?",
                (job.run_id,),
            )
            .fetchone()[0]
        )
        code_paths = (
            session["inventory"]
            .connection.execute(
                "SELECT code_path FROM conversion_candidate WHERE run_id=?",
                (job.run_id,),
            )
            .fetchall()
        )
    assert persisted == snapshot["object_count"]
    assert code_paths and all(row[0] is None or row[0].endswith(".json") for row in code_paths)
    encrypted_path = next(row[0] for row in code_paths if row[0] is not None)
    encrypted_bytes = session["store"]._resolve(encrypted_path).read_bytes()
    assert b"Converted:" not in encrypted_bytes
    assert b"Converted:" in session["store"].read(encrypted_path)
    event_text = "".join(job.event_stream())
    assert "event: convert.start" in event_text
    assert "event: convert.batch" in event_text
    assert "event: convert.escalation" in event_text
    assert "event: convert.complete" in event_text
    assert "Converted:" not in event_text
    audit_text = (tmp_path / target / "audit.jsonl").read_text(encoding="utf-8")
    assert all(
        f'"{action}"' in audit_text
        for action in (
            "convert.consent",
            "convert.start",
            "convert.batch",
            "convert.escalation",
            "convert.complete",
        )
    )
    assert "Converted:" not in audit_text


def test_conversion_job_continues_after_voter_failure_and_cancels_running_sessions(
    tmp_path: Path,
) -> None:
    session = _inventory_session(tmp_path / "failure", TERADATA_PACK, "teradata")
    session["target"] = "snowflake"
    pack = _target_pack("snowflake")
    clients = []
    scenario = MockScenario(fail_voters={"B"})

    def client_factory(voter: str) -> MockDevinClient:
        client = MockDevinClient(scenario)
        clients.append(client)
        return client

    job = ConversionJob(
        session=session,
        audit=None,
        batch_size=50,
        timeout_minutes=1,
        client_factory=client_factory,
        pack=pack,
    )
    job.start()
    job.join(timeout=180)
    assert job.snapshot()["state"] == "done"
    assert len(job.results) == job.snapshot()["object_count"]
    assert all(
        status in {"done", "failed"}
        for batch in job.snapshot()["progress"]["batches"].values()
        for status in batch["voters"].values()
        if status != "not_needed"
    )
    assert all(client.termination_calls for client in clients if client.sessions)

    cancel_session = _inventory_session(tmp_path / "cancel", TERADATA_PACK, "teradata")
    cancel_session["target"] = "snowflake"
    slow_clients = []
    slow = MockScenario(timeout_voters={"A"})

    def slow_factory(voter: str) -> MockDevinClient:
        client = MockDevinClient(slow)
        slow_clients.append(client)
        return client

    cancel_job = ConversionJob(
        session=cancel_session,
        audit=None,
        batch_size=50,
        timeout_minutes=5,
        client_factory=slow_factory,
        pack=pack,
    )
    cancel_job.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(client.sessions for client in slow_clients):
        time.sleep(0.01)
    assert any(client.sessions for client in slow_clients)
    cancel_job.cancel()
    cancel_job.join(timeout=30)
    assert not cancel_job.is_alive()
    assert cancel_job.snapshot()["state"] == "cancelled"
    assert all(client.termination_calls for client in slow_clients if client.sessions)


def test_informatica_conversion_uses_lazy_target_format_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("migraai.etl.outputs")
    module.formats_for = lambda target: [
        {
            "id": f"{target}.python",
            "rel_path": r"C:\restricted\mapping.py",
            "language": "python",
            "description": "Python mapping output.",
            "skeleton": "def run():\n    pass\n",
        }
    ]
    monkeypatch.setitem(__import__("sys").modules, "migraai.etl.outputs", module)
    session = _inventory_session(tmp_path, INFORMATICA_PACK, "informatica")
    session["target"] = "snowflake"
    pack = _target_pack("snowflake")
    clients = []

    def client_factory(voter: str) -> MockDevinClient:
        client = MockDevinClient()
        clients.append(client)
        return client

    job = ConversionJob(
        session=session,
        audit=None,
        batch_size=20,
        timeout_minutes=2,
        confidence_threshold=0.0,
        client_factory=client_factory,
        pack=pack,
    )
    job.start()
    job.join(timeout=120)
    assert not job.is_alive()
    assert job.snapshot()["state"] == "done"
    assert len(job.results) == job.snapshot()["object_count"]
    assert len(job.batches) == 3
    assert sum(len(client.sessions) for client in clients) == 9
    attachments = [content for client in clients for content in client.attachments.values()]
    assert attachments
    assert all("restricted" not in content for content in attachments)
    assert all(
        "Python mapping output." in content and "def run():" in content for content in attachments
    )
