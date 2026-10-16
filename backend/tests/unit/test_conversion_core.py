from __future__ import annotations

import importlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from migraai.agents import workflow
from migraai.agents.batching import Batch, plan_batches
from migraai.agents.context import build_batch
from migraai.agents.job import ConversionJob
from migraai.agents.naming import build_name_map
from migraai.api.convert import _estimate
from migraai.core import secret_mask
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
from migraai.etl.outputs import formats_for
from migraai.packaging.snapshot import build_package_input
from migraai.platforms.pack import TargetPack, load_pack
from migraai.reports.session_report import build_report_md
from migraai.validation.checks.security import _SECRET_PATTERNS
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


def test_name_map_excludes_grants_roles_and_users() -> None:
    name_map = build_name_map(
        [
            {"qualified_name": "SELECT ON sales.account", "type": "grant"},
            {"qualified_name": "sales.reader", "type": "role"},
            {"qualified_name": "sales.loader", "type": "user"},
            {"qualified_name": "sales.account", "type": "table"},
        ],
        load_pack("_example"),
    )

    assert name_map == {"sales.account": "sales.account"}


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
    assert "PASSWORD '***MASKED***'" in rendered[0]["source_text"]
    assert "IDENTIFIED BY ***MASKED***" in rendered[0]["source_text"]
    assert "hidden" not in attachment and "hidden_token" not in attachment
    assert "DO NOT ATTACH" not in attachment
    assert str(tmp_path) not in attachment
    assert "do-not-attach" not in attachment
    assert rendered[0]["rel_path"] == "relative/account.sql"


def test_conversion_context_masks_every_testpack_object(tmp_path: Path) -> None:
    testpacks = ROOT / "testpacks"
    quoted_passwords: set[str] = set()
    attachments: list[str] = []
    target_pack = load_pack("snowflake")
    pack_dirs = sorted(path.parent for path in testpacks.rglob("pack.yaml"))
    assert pack_dirs
    for index, pack_dir in enumerate(pack_dirs):
        metadata = (pack_dir / "pack.yaml").read_text(encoding="utf-8")
        kind = re.search(r"(?m)^kind:\s*(\S+)", metadata)
        dialect = re.search(r"(?m)^source_dialect:\s*(\S+)", metadata)
        assert kind and dialect
        source = pack_dir.name if kind.group(1) == "etl" else dialect.group(1)
        files = {
            path.relative_to(pack_dir).as_posix(): path.read_bytes()
            for path in pack_dir.rglob("*")
            if path.is_file() and path.name not in {"pack.yaml", "expected_inventory.yaml"}
        }
        for data in files.values():
            text = decode_source(data)[0]
            quoted_passwords.update(
                match.group(2)
                for match in re.finditer(
                    r"""(?i)(?:\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key|client[_-]?secret)\b\s*[=:]\s*|\bPASSWORD\b\s+|\bIDENTIFIED\s+BY(?:\s+VALUES)?\s*)(['"])(.*?)\1""",
                    text,
                )
            )
        inventory = Inventory()
        store = EncryptedFileStore(tmp_path / str(index) / "store", SessionCipher())
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
        parsed, edges = discover_files(files, source)
        inventory.replace_discovery(parsed, edges, uploads, previews)
        session = {"inventory": inventory, "store": store, "uploads": uploads}
        objects = [
            item for item in inventory.page(page_size=300)["items"] if not item.get("external")
        ]
        attachment, _ = build_batch(
            session,
            Batch(index=0, objects=objects),
            source,
            "snowflake",
            target_pack,
            build_name_map(objects, target_pack),
        )
        attachments.append(attachment)
        assert secret_mask.mask_secrets(attachment) == attachment, pack_dir
        assert not any(pattern.search(attachment) for pattern in _SECRET_PATTERNS), pack_dir
        assert "demo-secret" not in attachment
    assert all(value not in attachment for value in quoted_passwords for attachment in attachments)


def test_secret_guard_blocks_batch_before_upload_or_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source-pack"
    source_dir.mkdir()
    (source_dir / "user.sql").write_text(
        "CREATE USER private_user PASSWORD = 'guard-secret';",
        encoding="utf-8",
    )
    session = _inventory_session(tmp_path / "blocked", source_dir, "teradata")
    session["target"] = "snowflake"
    clients = []

    def client_factory(voter: str) -> MockDevinClient:
        client = MockDevinClient()
        clients.append(client)
        return client

    monkeypatch.setattr(secret_mask, "mask_secrets", lambda text: text)
    audit_path = tmp_path / "blocked" / "audit.jsonl"
    job = ConversionJob(
        session=session,
        audit=AuditLog(audit_path),
        batch_size=20,
        timeout_minutes=2,
        client_factory=client_factory,
        pack=load_pack("snowflake"),
    )
    object_id = str(job.objects[0]["id"])
    job.start()
    job.join(timeout=30)

    assert not job.is_alive()
    assert job.snapshot()["state"] == "failed"
    assert job.snapshot()["counts"]["failed"] == 1
    assert job.errors[object_id] == (
        "A possible secret was found in the code to send; nothing was sent for this batch."
    )
    assert job.batch_status[0]["status"] == "failed"
    assert clients == []
    assert sum(len(client.attachments) for client in clients) == 0
    assert sum(len(client.sessions) for client in clients) == 0
    audit_entries = [
        json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    blocked = next(entry for entry in audit_entries if entry["action"] == "convert.blocked_secret")
    assert blocked["details"] == {"batch_index": 0, "object_ids": [object_id]}
    assert "guard-secret" not in audit_path.read_text(encoding="utf-8")


def test_validator_exception_fails_only_the_affected_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "validator-errors"
    source_dir.mkdir()
    for name in ("good_a", "bad", "good_b"):
        (source_dir / f"{name}.sql").write_text(
            f"CREATE TABLE sales.{name} (id INTEGER);",
            encoding="utf-8",
        )
    session = _inventory_session(tmp_path / "validator-session", source_dir, "teradata")
    session["target"] = "snowflake"
    bad_object = next(
        item for item in session["inventory"].page(page_size=20)["items"] if item["name"] == "bad"
    )
    original_validate = workflow.validate_object

    def fail_one_object(obj, context):
        if obj.source_qualified_name == "sales.bad":
            raise RuntimeError("password=hunter2xyz")
        return original_validate(obj, context)

    monkeypatch.setattr(workflow, "validate_object", fail_one_object)
    job = ConversionJob(
        session=session,
        audit=None,
        batch_size=20,
        timeout_minutes=1,
        confidence_threshold=0.0,
        client_factory=lambda role: MockDevinClient(),
        pack=load_pack("snowflake"),
    )

    job.start()
    job.join(timeout=30)

    assert not job.is_alive()
    assert job.snapshot()["state"] == "done"
    assert job.results[str(bad_object["id"])]["status"] == "failed"
    assert all(
        result["status"] == "converted"
        for object_id, result in job.results.items()
        if object_id != str(bad_object["id"])
    )
    reason = job.errors[str(bad_object["id"])]
    assert "hunter2xyz" not in reason
    assert secret_mask.mask_secrets("password=hunter2xyz") in reason
    package = build_package_input(job, session, "test")
    packaged_bad_object = next(
        item for item in package.objects if item.object_id == bad_object["id"]
    )
    assert packaged_bad_object.status == "failed"
    assert packaged_bad_object.error == reason


def test_packaging_exception_fails_only_the_affected_object(tmp_path: Path) -> None:
    source_dir = tmp_path / "packaging-errors"
    source_dir.mkdir()
    for name in ("good_a", "bad", "good_b"):
        (source_dir / f"{name}.sql").write_text(
            f"CREATE TABLE sales.{name} (id INTEGER);",
            encoding="utf-8",
        )
    session = _inventory_session(tmp_path / "packaging-session", source_dir, "teradata")
    session["target"] = "snowflake"
    bad_object = next(
        item for item in session["inventory"].page(page_size=20)["items"] if item["name"] == "bad"
    )
    job = ConversionJob(
        session=session,
        audit=None,
        batch_size=20,
        timeout_minutes=1,
        confidence_threshold=0.0,
        client_factory=lambda role: MockDevinClient(),
        pack=load_pack("snowflake"),
    )
    store_json = job._store_json

    def fail_final_packaging(rel_path: str, value: Any) -> str:
        if rel_path.endswith(f"/{bad_object['id']}/final.json"):
            raise RuntimeError("password=hunter2xyz")
        return store_json(rel_path, value)

    job._store_json = fail_final_packaging
    job.start()
    job.join(timeout=30)

    bad_id = str(bad_object["id"])
    assert not job.is_alive()
    assert job.snapshot()["state"] == "done"
    assert job.results[bad_id]["status"] == "failed"
    assert all(
        result["status"] == "converted"
        for object_id, result in job.results.items()
        if object_id != bad_id
    )
    reason = job.errors[bad_id]
    assert "hunter2xyz" not in reason
    assert secret_mask.mask_secrets("password=hunter2xyz") in reason
    package = build_package_input(job, session, "test")
    packaged_bad_object = next(item for item in package.objects if item.object_id == bad_id)
    assert packaged_bad_object.status == "failed"
    assert packaged_bad_object.error == reason


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


@pytest.mark.parametrize("target", TARGETS)
def test_real_target_pack_loads(target: str) -> None:
    assert load_pack(target).target == target


def test_real_etl_formats_are_included_in_batch_context(tmp_path: Path) -> None:
    session = _inventory_session(tmp_path, INFORMATICA_PACK, "informatica")
    session["target"] = "snowflake"
    pack = load_pack("snowflake")
    rows = session["inventory"].page(page_size=300)["items"]
    workflow = next(row for row in rows if row["type"] == "workflow")
    attachment, _ = build_batch(
        session,
        Batch(index=0, objects=[workflow]),
        "informatica",
        "snowflake",
        pack,
        build_name_map(rows, pack),
    )

    item = parse_batch(attachment)[0]
    formats = item["output_formats"]
    expected = next(fmt for fmt in formats_for("snowflake") if fmt.language == "python")
    assert any(fmt["id"] == expected.id and fmt["skeleton"] == expected.skeleton for fmt in formats)


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


def test_single_file_candidates_use_source_path_for_agreement() -> None:
    from migraai.agents.workflow import _as_candidate

    source_path = "teradata-testpack.zip/tables.sql"
    content = "CREATE TABLE sales.card (card_id INTEGER);"
    candidates = {
        voter: _as_candidate(
            voter,
            "card",
            {
                "files": [
                    {
                        "rel_path": candidate_path,
                        "language": "sql",
                        "content": content,
                    }
                ]
            },
            "snowflake",
            source_rel_path=source_path,
        )
        for voter, candidate_path in (
            ("A", "tables/sales.card.sql"),
            ("B", "tables/SALES.CARD.sql"),
        )
    }

    assert candidates["A"] is not None
    assert candidates["B"] is not None
    assert candidates["A"].files[0]["rel_path"] == source_path
    assert candidates["B"].files[0]["rel_path"] == source_path
    assert choose(candidates, target="snowflake").agreement == 1.0


def test_procedure_dependency_context_includes_parameter_signatures(tmp_path: Path) -> None:
    session = _inventory_session(tmp_path, TERADATA_PACK, "teradata")
    inventory_rows = session["inventory"].page(page_size=300)["items"]
    grant = next(
        row
        for row in inventory_rows
        if row["qualified_name"] == "EXECUTE PROCEDURE on sales.add_payment"
    )

    attachment, _ = build_batch(
        session,
        Batch(index=0, objects=[grant]),
        "teradata",
        "snowflake",
        _target_pack("snowflake"),
        {},
    )

    assert '"sales.add_payment"' in attachment
    assert '"name": "p_account"' in attachment
    assert '"type": "BIGINT"' in attachment
    assert '"direction": "in"' in attachment
    assert '"name": "p_amount"' in attachment
    assert '"type": "DECIMAL(12, 2)"' in attachment


def test_conversion_prompts_preserve_path_and_data_type_rules() -> None:
    from migraai.devin.prompts import ESCALATION, VOTER_BASE

    assert (
        "For a single-file object, set rel_path to the object's source rel_path exactly as given "
        "in the attachment; use other paths only for the extra files an ETL output format "
        "requires. Use the data types from the type mappings exactly as written, and keep the "
        "source precision and scale unless a mapping says otherwise."
    ) in VOTER_BASE
    assert "naming, rel_path and data-type rules as the candidates." in ESCALATION


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


def test_teradata_to_snowflake_uses_real_target_pack(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = _inventory_session(tmp_path / "real-pack", TERADATA_PACK, "teradata")
    session["target"] = "snowflake"
    clients: list[MockDevinClient] = []

    def client_factory(voter: str) -> MockDevinClient:
        client = MockDevinClient()
        clients.append(client)
        return client

    job = ConversionJob(
        session=session,
        audit=AuditLog(tmp_path / "real-pack" / "audit.jsonl"),
        batch_size=20,
        timeout_minutes=2,
        max_concurrent_sessions=4,
        client_factory=client_factory,
        pack=load_pack("snowflake"),
    )
    inventory_page = session["inventory"].page(page_size=300)
    assert inventory_page["total"] == 57
    assert inventory_page["external_total"] == 1
    assert len(job.objects) == 56
    settings = SimpleNamespace(
        read=lambda: SimpleNamespace(
            conversion_batch_size=20,
            conversion_session_timeout_minutes=45,
            max_concurrent_sessions=4,
            token_kind="none",
            devin_org_id="",
            devin_api_base_url="https://api.devin.ai",
        )
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings_store=settings)))
    estimate = _estimate(request, session)
    assert estimate["objects"] == len(job.objects) == 56
    job.start()
    job.join(timeout=180)
    assert not job.is_alive()
    snapshot = job.snapshot()
    assert snapshot["state"] == "done"
    assert snapshot["object_count"] == 56
    assert len(job.results) == snapshot["object_count"]
    assert sum(len(client.sessions) for client in clients) >= 9
    assert "CREATE MACRO sales.refresh_daily" not in caplog.text
    finding_ids = {
        finding.rule_id
        for result in job.results.values()
        for check in result["checks"]
        for finding in check.findings
    }
    assert (
        not {
            "dependency.order",
            "object_reference.unreferenced",
            "semantic.type_mismatch",
        }
        & finding_ids
    )

    if snapshot["counts"]["escalated"] == snapshot["object_count"]:
        rule_ids = {
            finding.rule_id
            for result in job.results.values()
            for check in result["checks"]
            for finding in check.findings
            if finding.rule_id.startswith("snowflake.")
        }
        assert rule_ids


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
        batch_size=20,
        timeout_minutes=5,
        client_factory=slow_factory,
        pack=pack,
    )
    cancel_job.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(client.sessions for client in slow_clients):
        time.sleep(0.01)
    assert any(client.sessions for client in slow_clients)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(
        info["batch_index"] >= 1 for info in cancel_job.started_sessions.values()
    ):
        time.sleep(0.01)
    assert any(info["batch_index"] >= 1 for info in cancel_job.started_sessions.values())
    cancel_job.cancel()
    cancel_job.join(timeout=30)
    assert not cancel_job.is_alive()
    assert cancel_job.snapshot()["state"] == "cancelled"
    assert all(
        status != "queued"
        for batch in cancel_job.snapshot()["progress"]["batches"].values()
        for status in batch["voters"].values()
    )
    assert all(client.termination_calls for client in slow_clients if client.sessions)
    package = build_package_input(cancel_job, cancel_session, "")
    assert package.egress is not None
    assert package.egress.sessions == len(cancel_job.started_sessions)
    assert len(package.sessions) == len(cancel_job.started_sessions)
    cancelled_count = sum(
        info["state"] == "cancelled" for info in cancel_job.started_sessions.values()
    )
    assert sum(session.state == "cancelled" for session in package.sessions) == cancelled_count
    report = build_report_md(package)
    section = report[report.index("## 4. Data sent to Devin") : report.index("## 5.")]
    assert f"- Sessions: {len(cancel_job.started_sessions)}" in section
    assert section.count("cancelled") >= cancelled_count


def test_cancel_finalises_full_run_results_before_blocked_batch_returns(tmp_path: Path) -> None:
    source_dir = tmp_path / "source-pack"
    source_dir.mkdir()
    for name in ("a", "b", "c"):
        (source_dir / f"{name}.sql").write_text(
            f"CREATE TABLE sales.{name} (id INTEGER);",
            encoding="utf-8",
        )
    session = _inventory_session(tmp_path / "cancel-immediate", source_dir, "teradata")
    entered = threading.Event()
    release = threading.Event()

    class GatedMockClient(MockDevinClient):
        def get_session(self, session_id: str) -> dict[str, Any]:
            entered.set()
            if not release.wait(timeout=10):
                raise TimeoutError("Mock session gate timed out.")
            return super().get_session(session_id)

    job = ConversionJob(
        session=session,
        audit=AuditLog(tmp_path / "cancel-immediate" / "audit.jsonl"),
        batch_size=1,
        timeout_minutes=1,
        max_concurrent_sessions=4,
        client_factory=lambda _: GatedMockClient(),
        pack=_target_pack("snowflake"),
    )
    job.start()
    try:
        assert entered.wait(timeout=5)
        assert len(job.selected_objects) == 3

        job.cancel()

        counts_after_cancel = dict(job.counts)
        snapshot = job.snapshot()
        assert snapshot["state"] == "cancelled"
        assert snapshot["counts"] == {
            "converted": 0,
            "escalated": 0,
            "expert_review": 0,
            "failed": 0,
            "cancelled": 3,
        }
        assert len(job.results) == 3
        assert all(result["status"] == "cancelled" for result in job.results.values())
        assert all(
            state != "queued"
            for batch in snapshot["progress"]["batches"].values()
            for state in batch["voters"].values()
        )
        with session["inventory"].lock:
            persisted_state = (
                session["inventory"]
                .connection.execute(
                    "SELECT state FROM conversion_run WHERE id=?",
                    (job.run_id,),
                )
                .fetchone()["state"]
            )
        assert persisted_state == "cancelled"

        events = [
            json.loads(line) for line in job.audit.path.read_text(encoding="utf-8").splitlines()
        ]
        cancel_event = next(event for event in events if event["action"] == "convert.cancel")
        assert cancel_event["details"]["completed_count"] == 3

        release.set()
        job.join(timeout=30)
        assert not job.is_alive()
        assert job.counts == counts_after_cancel
        assert all(result["status"] == "cancelled" for result in job.results.values())
    finally:
        release.set()
        job.join(timeout=30)


def test_three_batch_mock_run_reports_nine_distinct_sessions(tmp_path: Path) -> None:
    source_dir = tmp_path / "three-batch-pack"
    source_dir.mkdir()
    for name in ("a", "b", "c"):
        (source_dir / f"{name}.sql").write_text(
            f"CREATE TABLE sales.{name} (id INTEGER);",
            encoding="utf-8",
        )
    session = _inventory_session(tmp_path / "three-batch", source_dir, "teradata")
    job = ConversionJob(
        session=session,
        audit=None,
        batch_size=1,
        timeout_minutes=1,
        confidence_threshold=0.0,
        max_concurrent_sessions=4,
        client_factory=lambda _: MockDevinClient(),
        pack=_target_pack("snowflake"),
    )

    job.start()
    job.join(timeout=30)

    assert not job.is_alive()
    assert len(job.batches) == 3
    assert len(job.started_sessions) == 9
    assert len(set(job.started_sessions)) == 9
    report = build_report_md(build_package_input(job, session, "test"))
    section = report[report.index("## 4. Data sent to Devin") : report.index("## 5.")]
    assert "- Sessions: 9" in section


def test_conversion_job_publishes_live_voter_and_escalation_progress(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source-pack"
    source_dir.mkdir()
    (source_dir / "orders.sql").write_text(
        "CREATE TABLE sales.orders (order_id INTEGER);",
        encoding="utf-8",
    )
    session = _inventory_session(tmp_path / "session", source_dir, "teradata")
    clients: list[MockDevinClient] = []
    voter_release = threading.Event()
    escalation_release = threading.Event()
    scenario = MockScenario()

    class GatedMockClient(MockDevinClient):
        def __init__(self, role: str) -> None:
            super().__init__(scenario)
            self.role = role

        def get_session(self, session_id: str) -> dict[str, object]:
            gate = (
                voter_release
                if self.role == "B"
                else escalation_release
                if self.role == "escalation"
                else None
            )
            if gate is not None and not gate.wait(timeout=10):
                raise TimeoutError("Mock session gate timed out.")
            return super().get_session(session_id)

    def client_factory(role: str) -> MockDevinClient:
        client = GatedMockClient(role)
        clients.append(client)
        return client

    job = ConversionJob(
        session=session,
        audit=None,
        batch_size=20,
        timeout_minutes=1,
        confidence_threshold=2.0,
        client_factory=client_factory,
        pack=_target_pack("snowflake"),
    )
    job.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            batch = job.snapshot()["progress"]["batches"][0]
            voter_states = batch["voters"]
            if voter_states["A"] == "done" and any(
                voter_states[voter] in {"running", "queued"} for voter in ("B", "C")
            ):
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"Voters did not expose partial progress: {job.snapshot()}")

        voter_release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            batch = job.snapshot()["progress"]["batches"][0]
            if batch["voters"]["escalation"] == "running":
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"Escalation did not expose running progress: {job.snapshot()}")

        assert {batch["voters"][voter] for voter in ("A", "B", "C")} == {"done"}
        assert batch["escalated"] is True
    finally:
        voter_release.set()
        escalation_release.set()
        job.join(timeout=10)

    assert not job.is_alive()
    final_batch = job.snapshot()["progress"]["batches"][0]
    assert job.snapshot()["state"] == "done"
    assert final_batch["voters"] == {
        "A": "done",
        "B": "done",
        "C": "done",
        "escalation": "done",
    }
    progress_events = [
        event["data"]
        for event in list(job.events.queue)
        if event is not None and event["event"] == "convert.batch" and "voter" in event["data"]
    ]
    assert any(event["voter"] == "A" and event["state"] == "done" for event in progress_events)
    assert any(event["voter"] == "B" and event["state"] == "running" for event in progress_events)
    assert any(
        event["voter"] == "escalation" and event["state"] == "running" for event in progress_events
    )


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
