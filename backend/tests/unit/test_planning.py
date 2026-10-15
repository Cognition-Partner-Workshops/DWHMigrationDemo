from __future__ import annotations

import inspect
import json
import threading
import time
import zipfile
from collections import Counter
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from pypdf import PdfReader

from migraai.core.audit import AuditLog
from migraai.devin.client import DevinClient, DevinV1Client, normalize_base_url
from migraai.kb.embed import HashEmbedder
from migraai.kb.store import KBStore, connect, insert_chunk
from migraai.planning.context import build_context
from migraai.planning.job import PlanJob
from migraai.planning.local_draft import build_local_draft
from migraai.planning.pdf import render_markdown, render_plan_pdf
from migraai.planning.prompt import build_prompt, inventory_summary_block
from migraai.planning.schema import SCHEMA
from migraai.planning.sections import SECTIONS
from migraai.planning.store import PlanStore


def _stores(tmp_path: Path):
    kb_store = KBStore(tmp_path / "data")
    kb_store.create_empty()
    plan_store = PlanStore(tmp_path / "data" / "app.db")
    plan_store.initialize()
    audit = AuditLog(tmp_path / "data" / "audit.jsonl")
    return kb_store, plan_store, audit


def test_sections_schema_context_and_local_draft(tmp_path: Path) -> None:
    store, _, _ = _stores(tmp_path)
    db_path = store.current_path
    context = build_context("teradata", "snowflake", db_path)
    plan = build_local_draft("teradata", "snowflake", db_path)

    assert len(SECTIONS) == 21
    assert [(section.id, section.title) for section in plan.sections] == SECTIONS
    assert plan.sections[0].content == "No curated knowledge for this pair yet."
    assert plan.sections[6].content == "No curated knowledge for this pair yet."
    assert plan.sections[16].platform_level_estimate
    assert plan.sections[18].platform_level_estimate
    assert plan.sections[19].platform_level_estimate
    assert plan.overall_confidence is None
    assert len(json.dumps(SCHEMA)) < 64 * 1024
    source = inspect.getsource(__import__("migraai.planning.context", fromlist=["build_context"]))
    assert "migraai.core.storage" not in source
    assert "migraai.core.crypto" not in source
    assert "session" not in source.casefold()
    assert context.kb_ids == set()


def test_local_draft_uses_pair_claims_mappings_and_risks(tmp_path: Path) -> None:
    store, _, _ = _stores(tmp_path)
    database = store.current_path
    embedder = HashEmbedder()
    claims = [
        ("teradata", "Teradata architecture profile", "architecture", None, None),
        (None, "Pair architecture overview", "architecture_overview", "teradata", "snowflake"),
        (None, "Storage differs between platforms", "storage", "teradata", "snowflake"),
        (None, "Tables map from source to target", "object_mapping", "teradata", "snowflake"),
        ("teradata", "BYTEINT maps to canonical int8", "type_mappings", None, None),
        ("snowflake", "NUMBER maps to canonical int64", "type_mappings", None, None),
    ]
    ids = {}
    with connect(database) as connection:
        for platform, text, section, source, target in claims:
            vector = embedder.embed([text])[0]
            url = (
                "https://docs.example/type"
                if section == "type_mappings"
                else f"https://docs.example/{section}"
            )
            chunk_id = insert_chunk(
                connection,
                (
                    None,
                    text,
                    url,
                    "2026-10-15",
                    platform,
                    source,
                    target,
                    section,
                    None,
                    "high",
                ),
                vector,
            )
            ids[section] = chunk_id
        connection.execute(
            "INSERT INTO pair_text VALUES(?,?,?,?)",
            (
                "teradata",
                "snowflake",
                "recommended_strategy",
                json.dumps("Migrate by domain after compatibility review."),
            ),
        )
        connection.execute(
            "INSERT INTO pair_text VALUES(?,?,?,?)",
            (
                "teradata",
                "snowflake",
                "object_mapping",
                json.dumps(
                    [
                        {
                            "source_object": "TABLE",
                            "target_object": "TABLE",
                            "notes": "Direct mapping",
                            "claim": {
                                "text": "Tables map from source to target",
                                "url": "https://docs.example/object_mapping",
                            },
                        }
                    ]
                ),
            ),
        )
        connection.execute(
            "INSERT INTO pair_text VALUES(?,?,?,?)",
            (
                "teradata",
                "snowflake",
                "risks",
                json.dumps(
                    {
                        "sql": {"level": "medium", "notes": "Review dialect differences"},
                        "data": {"level": "low", "notes": "Validate counts"},
                        **{
                            f"risk_{index}": {
                                "level": "medium",
                                "notes": f"Review risk {index}",
                            }
                            for index in range(13)
                        },
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO platform_hosts VALUES(?,?)",
            ("teradata", "docs.example"),
        )
        connection.execute(
            "INSERT INTO mappings VALUES(?,?,?,?,?,?)",
            ("teradata", "type", "BYTEINT", "int8", "small integer", "https://docs.example/type"),
        )
        connection.execute(
            "INSERT INTO mappings VALUES(?,?,?,?,?,?)",
            (
                "snowflake",
                "type",
                "NUMBER(38,0)",
                "int64",
                "wide integer",
                "https://docs.example/type",
            ),
        )
        connection.execute(
            """INSERT INTO catalog_rows
            VALUES(?,?,?,?,?,?,?)""",
            ("teradata", "mandatory", "tables", "Tables", "Table list", "[]", "SELECT"),
        )

    context = build_context("teradata", "snowflake", database)
    plan = build_local_draft("teradata", "snowflake", database)
    by_id = {section.id: section for section in plan.sections}
    assert "Teradata architecture profile" in by_id["A"].content
    assert "Pair architecture overview" in by_id["A"].content
    assert "### Teradata" in by_id["A"].content
    assert "### Teradata → Snowflake differences" in by_id["A"].content
    assert "([source](https://docs.example/architecture))" in by_id["A"].content
    assert "[teradata | architecture]" not in by_id["A"].content
    assert {item.kb_id for item in by_id["A"].citations} == {
        ids["architecture"],
        ids["architecture_overview"],
    }
    assert "Storage differs" in by_id["B"].content
    assert by_id["B"].citations[0].kb_id == ids["storage"]
    assert "([source](https://docs.example/object_mapping))" in by_id["G"].content
    assert by_id["G"].citations[0].kb_id == ids["object_mapping"]
    assert "### Teradata → Snowflake differences" in by_id["R"].content
    assert "**sql (medium):** Review dialect differences" in by_id["R"].content
    risk_bullets = by_id["R"].content.split("\n\n", 1)[1].splitlines()
    assert sum(line.startswith("- ") for line in risk_bullets) == 12
    assert "BYTEINT (https://docs.example/type)" in by_id["I"].content
    assert "no direct equivalent" in by_id["I"].content
    assert len(by_id["I"].citations) == 2
    assert "Tables" in context.markdown
    assert "docs.example" in context.allowed_hosts
    assert ids["architecture_overview"] in context.kb_ids


def test_local_draft_markdown_groups_links_and_caps_claims(tmp_path: Path) -> None:
    store, _, _ = _stores(tmp_path)
    database = store.current_path
    embedder = HashEmbedder()
    with connect(database) as connection:
        for index in range(13):
            text = f"[teradata | architecture] Teradata architecture point {index}"
            vector = embedder.embed([text])[0]
            insert_chunk(
                connection,
                (
                    None,
                    text,
                    f"https://docs.example/teradata/{index}",
                    "2026-10-15",
                    "teradata",
                    None,
                    None,
                    "architecture",
                    None,
                    "low" if index == 0 else "high",
                ),
                vector,
            )
        target_text = "[snowflake | architecture] Snowflake architecture profile"
        insert_chunk(
            connection,
            (
                None,
                target_text,
                "https://docs.example/snowflake",
                "2026-10-15",
                "snowflake",
                None,
                None,
                "architecture",
                None,
                "high",
            ),
            embedder.embed([target_text])[0],
        )
        pair_text = "[teradata → snowflake | architecture_overview] Pair architecture note"
        insert_chunk(
            connection,
            (
                None,
                pair_text,
                "https://docs.example/pair",
                "2026-10-15",
                None,
                "teradata",
                "snowflake",
                "architecture_overview",
                None,
                "high",
            ),
            embedder.embed([pair_text])[0],
        )

    plan = build_local_draft("teradata", "snowflake", database)
    content = next(section.content for section in plan.sections if section.id == "A")
    assert "### Teradata\n" in content
    assert "### Snowflake\n" in content
    assert "### Teradata → Snowflake differences" in content
    assert "([source](https://docs.example/teradata/0)) **(Manual review)**" in content
    assert "([source](https://docs.example/pair))" in content
    assert not any(
        prefix in content for prefix in ("[teradata | architecture]", "[snowflake | architecture]")
    )
    source_claims = content.split("### Teradata\n\n", 1)[1].split("\n\n### ", 1)[0]
    assert sum(line.startswith("- ") for line in source_claims.splitlines()) == 12


def test_devin_client_v3_request_shapes_and_url_normalization() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/attachments"):
            return httpx.Response(
                200,
                json={
                    "attachment_id": "att-1",
                    "name": "context.md",
                    "url": "https://files.test/a",
                },
                request=request,
            )
        if path.endswith("/sessions") and request.method == "POST":
            return httpx.Response(
                200,
                json={"session_id": "devin-123", "url": "https://devin.test/123"},
                request=request,
            )
        if request.method == "GET":
            return httpx.Response(
                200, json={"session_id": "devin-123", "status": "running"}, request=request
            )
        return httpx.Response(204, request=request)

    assert (
        normalize_base_url("https://api.devinenterprise.com/v1/sessions?x=1#part")
        == "https://api.devinenterprise.com"
    )
    assert (
        normalize_base_url("https://api.example.test:8443/path/") == "https://api.example.test:8443"
    )
    with pytest.raises(ValueError, match="organization ID"):
        DevinClient("https://api.example.test", "invalid-org", "cog_secret")
    client = DevinClient(
        "https://api.devinenterprise.com/v1/sessions?x=1",
        "org-demo",
        "cog_secret",
        transport=httpx.MockTransport(handler),
    )
    attachment = client.upload_attachment("context.md", b"# context")
    prompt = f'Build the plan.\nATTACHMENT:"{attachment}"\n'
    session_id, session_url = client.create_session(
        prompt,
        schema={"type": "object"},
        max_acu_limit=10,
        title="Migration plan",
        tags=["migraai"],
    )
    assert client.get_session(session_id) == {
        "state": "working",
        "detail": "running",
        "structured_output": None,
    }
    client.terminate_session(session_id)
    assert session_url == "https://devin.test/123"
    assert all(request.headers["authorization"] == "Bearer cog_secret" for request in requests)
    assert all(request.url.host == "api.devinenterprise.com" for request in requests)
    upload = requests[0]
    assert b'name="file"' in upload.read()
    create = json.loads(requests[1].content)
    assert create["attachment_urls"] == [attachment]
    assert create["resumable"] is False
    assert create["structured_output_required"] is True
    assert create["max_acu_limit"] == 10
    assert requests[-1].method == "DELETE"
    client.close()


def test_devin_v1_client_request_shapes_and_string_attachment_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/v1/attachments":
            return httpx.Response(200, json="https://files.test/context", request=request)
        if request.method == "POST" and request.url.path == "/v1/sessions":
            return httpx.Response(
                200,
                json={
                    "session_id": "legacy-session",
                    "url": "https://devin.test/legacy-session",
                    "is_new_session": True,
                },
                request=request,
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"status": "Working", "status_enum": "working", "structured_output": None},
                request=request,
            )
        return httpx.Response(204, request=request)

    client = DevinV1Client(
        "https://api.devinenterprise.com/v1/sessions?ignored=yes",
        "apk_secret",
        transport=httpx.MockTransport(handler),
    )
    attachment = client.upload_attachment("context.md", b"# context")
    prompt = f'Build the plan.\nATTACHMENT:"{attachment}"\n'
    session_id, session_url = client.create_session(
        prompt,
        schema={"type": "object"},
        max_acu_limit=10,
        title="Migration plan",
        tags=["migraai"],
    )
    assert client.get_session(session_id)["state"] == "working"
    client.terminate_session(session_id)
    assert session_url == "https://devin.test/legacy-session"
    assert [request.url.path for request in requests] == [
        "/v1/attachments",
        "/v1/sessions",
        "/v1/sessions/legacy-session",
        "/v1/sessions/legacy-session",
    ]
    assert all(request.headers["authorization"] == "Bearer apk_secret" for request in requests)
    assert b'name="file"' in requests[0].read()
    create = json.loads(requests[1].content)
    assert create == {
        "prompt": prompt,
        "structured_output_schema": {"type": "object"},
        "max_acu_limit": 10,
        "title": "Migration plan",
        "tags": ["migraai"],
        "idempotent": False,
    }
    assert "attachment_urls" not in create
    assert "structured_output_required" not in create
    assert requests[-1].method == "DELETE"
    client.close()

    dict_response_client = DevinV1Client(
        "https://api.example.test",
        "apk_key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"url": "https://files.test/dict"}, request=request
            )
        ),
    )
    assert (
        dict_response_client.upload_attachment("context.md", b"context")
        == "https://files.test/dict"
    )
    dict_response_client.close()


def test_devin_clients_omit_uncapped_session_limit() -> None:
    create_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/sessions"):
            create_requests.append(request)
            return httpx.Response(
                200,
                json={"session_id": "devin-test", "url": "https://devin.test/session"},
                request=request,
            )
        return httpx.Response(204, request=request)

    clients = [
        DevinClient(
            "https://api.example.test",
            "org-test",
            "cog_key",
            transport=httpx.MockTransport(handler),
        ),
        DevinV1Client(
            "https://api.example.test",
            "apk_key",
            transport=httpx.MockTransport(handler),
        ),
    ]
    for client in clients:
        client.create_session(
            "Convert this.",
            schema={"type": "object"},
            max_acu_limit=None,
            title="Conversion",
            tags=["migraai"],
        )
        client.close()

    assert len(create_requests) == 2
    assert all("max_acu_limit" not in json.loads(request.content) for request in create_requests)


def test_devin_v3_reads_documented_acu_usage_and_v1_has_none() -> None:
    def v3_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "running", "acus_consumed": 2.5},
            request=request,
        )

    v3_client = DevinClient(
        "https://api.example.test",
        "org-test",
        "cog_key",
        transport=httpx.MockTransport(v3_handler),
    )
    assert v3_client.get_session("devin-test")["acus"] == 2.5
    v3_client.close()

    def v1_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "Working", "status_enum": "working"},
            request=request,
        )

    v1_client = DevinV1Client(
        "https://api.example.test",
        "apk_key",
        transport=httpx.MockTransport(v1_handler),
    )
    assert "acus" not in v1_client.get_session("legacy-session")
    v1_client.close()


def test_shared_prompt_requires_structured_output() -> None:
    prompt = build_prompt("teradata", "snowflake", "https://files.test/context")
    assert (
        "You MUST finish by calling provide_structured_output with the complete plan; "
        "do not end without it."
    ) in prompt


def test_inventory_prompt_block_contains_aggregate_counts_only() -> None:
    aggregates = {
        "objects_by_type": {"table": 2, "view": 1},
        "parse_status_counts": {"parsed": 2, "unparsed": 1},
        "files_by_detected_type": {"sql:ansi": 2},
        "total_dependencies": 3,
        "dependencies_by_kind": {"reads": 2, "writes": 1},
        "external_reference_count": 1,
        "issues_by_severity": {"warning": 1},
        "total_source_lines": 42,
    }

    block = inventory_summary_block(aggregates)
    assert block == "\n".join(
        [
            "Inventory summary (aggregate counts only)",
            "Object counts by type: table=2, view=1",
            "Parse-status counts: parsed=2, unparsed=1",
            "File counts by detected type: sql:ansi=2",
            "Total dependencies: 3",
            "Dependency counts by kind: reads=2, writes=1",
            "External-reference count: 1",
            "Issue counts by severity: warning=1",
            "Total source lines: 42",
        ]
    )
    prompt = build_prompt("teradata", "snowflake", "https://files.test/context", aggregates)
    assert block in prompt
    assert "Ground effort, confidence, and expert-review counts in the inventory summary." in prompt
    assert "only aggregate inventory counts are available" in prompt


def test_local_draft_uses_inventory_aggregates_for_q_s_t(tmp_path: Path) -> None:
    store, _, _ = _stores(tmp_path)
    aggregates = {
        "objects_by_type": {"procedure": 1, "table": 2, "view": 1},
        "parse_status_counts": {"parsed": 2, "partial": 1, "unparsed": 1},
        "files_by_detected_type": {"sql:ansi": 2},
        "total_dependencies": 1,
        "dependencies_by_kind": {"calls": 1},
        "external_reference_count": 2,
        "issues_by_severity": {"error": 1, "warning": 2},
        "total_source_lines": 40,
    }

    plan = build_local_draft(
        "teradata", "snowflake", store.current_path, inventory_aggregates=aggregates
    )
    sections = {section.id: section.content for section in plan.sections}
    assert "approximately 3.6 person-days across 4 objects" in sections["Q"]
    assert "procedure=1, table=2, view=1" in sections["Q"]
    assert "Aggregate inventory confidence: 50%" in sections["S"]
    assert "unparsed share 25%, partial share 25%" in sections["S"]
    assert "1 unparsed objects, 1 partial objects, 2 external references" in sections["T"]
    assert "error=1, warning=2" in sections["T"]


def _upload_ansi_sql_pack(client: TestClient) -> tuple[dict, Path, list[str]]:
    host = {"Host": "127.0.0.1:80"}
    launched = client.get(
        "/api/auth/launch",
        params={"token": "test-launch-token"},
        headers=host,
        follow_redirects=False,
    )
    assert launched.status_code == 303
    headers = {**host, "X-MigraAI-CSRF": str(client.cookies["migraai_csrf"])}
    confirmed = client.post(
        "/api/session/confirm",
        json={"source": "teradata", "target": "snowflake"},
        headers=headers,
    )
    assert confirmed.status_code == 200
    session = client.app.state.current_session
    session["source"] = "ansi"
    empty_fingerprint = session["inventory"].fingerprint()

    pack_dir = Path(__file__).resolve().parents[3] / "testpacks" / "ansi"
    files = sorted(pack_dir.glob("*.sql"))
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in files:
            zipped.write(path, arcname=path.name)
    response = client.post(
        "/api/uploads",
        files=[("files", ("ansi-pack.zip", archive.getvalue(), "application/zip"))],
        headers=headers,
    )
    assert response.status_code == 200
    session["discovery"]._queue.join()
    assert session["discovery"].status()["state"] == "done"
    assert session["inventory"].fingerprint() != empty_fingerprint
    return session, pack_dir, [path.name for path in files]


@pytest.mark.parametrize(("pat", "org_id"), [("apk_test", ""), ("cog_test", "org-test")])
def test_uploaded_ansi_inventory_is_only_sent_as_aggregates(
    client: TestClient, pat: str, org_id: str
) -> None:
    session, pack_dir, file_names = _upload_ansi_sql_pack(client)
    aggregates = session["inventory"].aggregates()
    fingerprint = session["inventory"].fingerprint()
    expected = yaml.safe_load((pack_dir / "expected_inventory.yaml").read_text())
    expected_objects = expected["objects"]
    expected_edges = expected["edges"]

    assert aggregates["objects_by_type"] == dict(Counter(item["type"] for item in expected_objects))
    assert aggregates["parse_status_counts"] == dict(
        Counter(item["status"] for item in expected_objects)
    )
    assert aggregates["files_by_detected_type"] == {"sql:ansi": len(file_names)}
    assert aggregates["total_dependencies"] == len(expected_edges)
    assert aggregates["dependencies_by_kind"] == dict(
        Counter(item["kind"] for item in expected_edges)
    )
    assert aggregates["external_reference_count"] == sum(
        bool(item["external"]) for item in expected_edges
    )
    assert aggregates["total_source_lines"] == sum(
        len((pack_dir / file_name).read_text(encoding="utf-8").splitlines())
        for file_name in file_names
    )
    assert set(aggregates) == {
        "objects_by_type",
        "parse_status_counts",
        "files_by_detected_type",
        "total_dependencies",
        "dependencies_by_kind",
        "external_reference_count",
        "issues_by_severity",
        "total_source_lines",
    }
    assert fingerprint

    kb_store = client.app.state.kb_store
    fake = FakeClient(
        [
            {
                "state": "done",
                "detail": "finished",
                "structured_output": build_local_draft(
                    "teradata", "snowflake", kb_store.current_path
                ).model_dump(),
            }
        ]
    )
    job = PlanJob(
        source="teradata",
        target="snowflake",
        db_path=kb_store.current_path,
        kb_store=kb_store,
        plan_store=client.app.state.plan_store,
        audit=client.app.state.audit_log,
        base_url="https://api.example.test",
        org_id=org_id,
        pat=pat,
        client_factory=lambda *_: fake,
        inventory_aggregates=aggregates,
        inventory_fingerprint=fingerprint,
    )
    job.cancel_event = ImmediateEvent()
    job.run()

    assert job.snapshot()["state"] == "done"
    assert len(fake.prompts) == 1
    assert "Inventory summary (aggregate counts only)" in fake.prompts[0]
    assert "Object counts by type:" in fake.prompts[0]
    assert "Total source lines:" in fake.prompts[0]
    outbound_body = (
        json.dumps(fake.requests, ensure_ascii=False, default=str)
        + "\n"
        + "\n".join(data.decode("utf-8") for _, data in fake.attachments)
    ).casefold()
    expected_names = [str(item["qualified_name"]) for item in expected["objects"]]
    snippets = []
    for file_name in file_names:
        lines = (pack_dir / file_name).read_text().splitlines()
        snippets.append(next(line.strip() for line in lines if line.strip()))
    for canary in [*expected_names, *file_names, *snippets]:
        assert canary.casefold() not in outbound_body, canary


@pytest.mark.parametrize(
    ("status_enum", "expected"),
    [
        ("working", "working"),
        ("blocked", "blocked"),
        ("expired", "failed"),
        ("finished", "done"),
        ("suspend_requested", "failed"),
        ("suspend_requested_frontend", "failed"),
        ("resume_requested", "working"),
        ("resume_requested_frontend", "working"),
        ("resumed", "working"),
        (None, "working"),
    ],
)
def test_devin_v1_session_state_mapping(status_enum: str | None, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "Raw status",
                "status_enum": status_enum,
                "structured_output": {"result": "plan"},
            },
            request=request,
        )

    client = DevinV1Client(
        "https://api.example.test",
        "apk_key",
        transport=httpx.MockTransport(handler),
    )
    result = client.get_session("legacy-session")
    assert result == {
        "state": expected,
        "detail": "Raw status" + (f"; {status_enum}" if status_enum else ""),
        "structured_output": {"result": "plan"},
    }
    client.close()


@pytest.mark.parametrize(
    ("status", "status_detail", "expected"),
    [
        ("exit", None, "done"),
        ("running", "finished", "done"),
        ("running", "waiting_for_user", "blocked"),
        ("running", "waiting_for_approval", "blocked"),
        ("error", "worker failed", "failed"),
        ("suspended", None, "failed"),
        ("running", None, "working"),
    ],
)
def test_devin_v3_session_state_mapping(
    status: str, status_detail: str | None, expected: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": status,
                "status_detail": status_detail,
                "structured_output": {"result": "plan"},
            },
            request=request,
        )

    client = DevinClient(
        "https://api.example.test",
        "org-test",
        "cog_key",
        transport=httpx.MockTransport(handler),
    )
    result = client.get_session("devin-123")
    assert result["state"] == expected
    expected_detail = "; ".join(value for value in (status, status_detail) if value)
    assert result["detail"] == expected_detail
    assert result["structured_output"] == {"result": "plan"}
    client.close()


class FakeClient:
    def __init__(
        self,
        responses: list[dict],
        created: threading.Event | None = None,
        create_release: threading.Event | None = None,
        terminate_error: Exception | None = None,
    ) -> None:
        self.responses = responses
        self.created = created
        self.create_release = create_release
        self.terminate_error = terminate_error
        self.terminated: list[str] = []
        self.closed = False
        self.attachments: list[tuple[str, bytes]] = []
        self.prompts: list[str] = []
        self.requests: list[dict] = []

    def upload_attachment(self, name: str, data: bytes) -> str:
        self.attachments.append((name, data))
        return "https://files.test/context"

    def create_session(self, prompt: str, **kwargs) -> tuple[str, str]:
        self.prompts.append(prompt)
        self.requests.append({"prompt": prompt, **kwargs})
        if self.created:
            self.created.set()
        if self.create_release:
            self.create_release.wait(2)
        return "devin-test", "https://devin.test/session"

    def get_session(self, session_id: str) -> dict:
        return self.responses.pop(0)

    def terminate_session(self, session_id: str) -> None:
        self.terminated.append(session_id)
        if self.terminate_error:
            raise self.terminate_error

    def close(self) -> None:
        self.closed = True


def _v1_job(
    tmp_path: Path,
    responses: list[dict],
    *,
    requests: list[httpx.Request] | None = None,
    session_created: threading.Event | None = None,
    session_release: threading.Event | None = None,
    terminate_status: int = 204,
) -> tuple[PlanJob, KBStore, list[httpx.Request]]:
    kb_store, plan_store, audit = _stores(tmp_path)
    captured = requests if requests is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "POST" and request.url.path == "/v1/attachments":
            return httpx.Response(200, json="https://files.test/context", request=request)
        if request.method == "POST" and request.url.path == "/v1/sessions":
            if session_created:
                session_created.set()
            if session_release:
                session_release.wait(2)
            return httpx.Response(
                200,
                json={
                    "session_id": "legacy-session",
                    "url": "https://devin.test/legacy-session",
                    "is_new_session": True,
                },
                request=request,
            )
        if request.method == "GET":
            result = responses.pop(0)
            if result.get("structured_output") == "local-draft":
                result["structured_output"] = build_local_draft(
                    "teradata", "snowflake", kb_store.current_path
                ).model_dump()
            return httpx.Response(200, json=result, request=request)
        if request.method == "DELETE" and terminate_status != 204:
            return httpx.Response(
                terminate_status,
                json={"detail": "termination failed"},
                request=request,
            )
        return httpx.Response(204, request=request)

    job = PlanJob(
        source="teradata",
        target="snowflake",
        db_path=kb_store.current_path,
        kb_store=kb_store,
        plan_store=plan_store,
        audit=audit,
        base_url="https://api.devinenterprise.com",
        org_id="",
        pat="apk_legacy",
        client_factory=lambda base_url, _org_id, api_key: DevinV1Client(
            base_url, api_key, transport=httpx.MockTransport(handler)
        ),
    )
    return job, kb_store, captured


def _job(tmp_path: Path, fake: FakeClient, **kwargs) -> PlanJob:
    kb_store, plan_store, audit = _stores(tmp_path)
    timeout_minutes = kwargs.pop("timeout_minutes", 30)
    org_id = kwargs.pop("org_id", "org-test")
    pat = kwargs.pop("pat", "cog_test")
    client_factory = kwargs.pop("client_factory", lambda *_: fake)
    return PlanJob(
        source="teradata",
        target="snowflake",
        db_path=kb_store.current_path,
        kb_store=kb_store,
        plan_store=plan_store,
        audit=audit,
        base_url="https://api.devinenterprise.com",
        org_id=org_id,
        pat=pat,
        timeout_minutes=timeout_minutes,
        client_factory=client_factory,
        **kwargs,
    )


class ImmediateEvent:
    def __init__(self) -> None:
        self.cancelled = False

    def is_set(self) -> bool:
        return self.cancelled

    def set(self) -> None:
        self.cancelled = True

    def wait(self, timeout: float) -> bool:
        return self.cancelled


def test_v1_job_finished_and_blocked_outputs_complete(tmp_path: Path) -> None:
    for index, enum in enumerate(("finished", "blocked")):
        job, store, requests = _v1_job(
            tmp_path / str(index),
            [{"status": "Raw status", "status_enum": enum, "structured_output": "local-draft"}],
        )
        job.cancel_event = ImmediateEvent()
        job.run()
        assert job.snapshot()["state"] == "done"
        assert sum(request.method == "DELETE" for request in requests) == 1


def test_v1_job_blocked_twice_and_expired_fall_back(tmp_path: Path) -> None:
    blocked, _, blocked_requests = _v1_job(
        tmp_path / "blocked",
        [
            {"status": "Blocked", "status_enum": "blocked"},
            {"status": "Blocked", "status_enum": "blocked"},
        ],
    )
    blocked.cancel_event = ImmediateEvent()
    blocked.run()
    assert blocked.snapshot()["reason"] == "Devin asked for input"
    assert sum(request.method == "DELETE" for request in blocked_requests) == 1

    expired, _, expired_requests = _v1_job(
        tmp_path / "expired",
        [{"status": "Expired", "status_enum": "expired"}],
    )
    expired.cancel_event = ImmediateEvent()
    expired.run()
    assert expired.snapshot()["reason"] == "Devin session ended: Expired; expired"
    assert sum(request.method == "DELETE" for request in expired_requests) == 1


def test_v1_job_termination_failure_keeps_completed_plan(tmp_path: Path) -> None:
    job, _, requests = _v1_job(
        tmp_path,
        [{"status": "Finished", "status_enum": "finished", "structured_output": "local-draft"}],
        terminate_status=500,
    )
    job.cancel_event = ImmediateEvent()
    job.run()

    assert job.snapshot()["state"] == "done"
    assert sum(request.method == "DELETE" for request in requests) == 1
    records = [json.loads(line) for line in job.audit.path.read_text().splitlines()]
    failure = next(record for record in records if record["action"] == "plan.terminate_failed")
    assert failure["target"] == "legacy-session"
    assert "token" not in json.dumps(failure).casefold()


def test_v1_job_cancel_issues_delete(tmp_path: Path) -> None:
    created = threading.Event()
    job, _, requests = _v1_job(tmp_path, [], session_created=created)
    job.start()
    assert created.wait(2)
    deadline = time.monotonic() + 2
    while job.session_id is None and time.monotonic() < deadline:
        time.sleep(0.001)
    job.cancel()
    job.join(2)
    assert job.snapshot()["state"] == "cancelled"
    assert any(
        request.method == "DELETE" and request.url.path == "/v1/sessions/legacy-session"
        for request in requests
    )
    assert sum(request.method == "DELETE" for request in requests) == 1


def test_v1_job_cancel_during_session_creation_terminates_once(tmp_path: Path) -> None:
    created = threading.Event()
    release = threading.Event()
    job, _, requests = _v1_job(
        tmp_path,
        [],
        session_created=created,
        session_release=release,
    )
    job.start()
    assert created.wait(2)

    job.cancel()
    release.set()
    job.join(2)

    assert not job.is_alive()
    assert job.snapshot()["state"] == "cancelled"
    assert sum(request.method == "DELETE" for request in requests) == 1


def test_plan_job_validates_citations_and_saves_devin_plan(tmp_path: Path) -> None:
    kb_store, plan_store, audit = _stores(tmp_path)
    draft = build_local_draft("teradata", "snowflake", kb_store.current_path).model_dump()
    draft["sections"][0]["citations"] = [
        {"kb_id": 999, "note": "unknown"},
        {"url": "https://untrusted.example/doc", "note": "external"},
    ]
    fake = FakeClient([{"state": "done", "detail": "exit", "structured_output": draft}])
    job = PlanJob(
        source="teradata",
        target="snowflake",
        db_path=kb_store.current_path,
        kb_store=kb_store,
        plan_store=plan_store,
        audit=audit,
        base_url="https://api.example.test",
        org_id="org-1",
        pat="cog_1",
        client_factory=lambda *_: fake,
    )
    job.cancel_event = ImmediateEvent()
    job.run()

    saved = plan_store.latest("teradata", "snowflake", origin="devin")
    assert job.snapshot()["state"] == "done"
    assert saved is not None
    first = saved["payload"]["sections"][0]
    assert first["citations"] == [
        {
            "kb_id": None,
            "url": "https://untrusted.example/doc",
            "note": "external",
            "unverified": True,
        }
    ]
    assert first["unverified_citations"] is True
    assert fake.terminated == ["devin-test"]


@pytest.mark.parametrize(
    ("status", "detail", "reason"),
    [
        ("error", "worker failed", "Devin session ended: error; worker failed"),
        ("suspended", "paused", "Devin session ended: suspended; paused"),
    ],
)
def test_plan_job_error_statuses_fall_back(
    tmp_path: Path, status: str, detail: str, reason: str
) -> None:
    fake = FakeClient([{"state": "failed", "detail": f"{status}; {detail}"}])
    job = _job(tmp_path, fake)
    job.cancel_event = ImmediateEvent()
    job.run()
    assert job.snapshot()["state"] == "failed_fallback"
    assert job.snapshot()["reason"] == reason
    assert job.snapshot()["origin"] == "local_draft"
    assert fake.terminated == ["devin-test"]


def test_plan_job_waiting_invalid_and_timeout_fallbacks(tmp_path: Path) -> None:
    fake = FakeClient(
        [
            {"state": "blocked", "detail": "running; waiting_for_user"},
            {"state": "blocked", "detail": "running; waiting_for_user"},
        ]
    )
    waiting = _job(tmp_path / "wait", fake)
    waiting.cancel_event = ImmediateEvent()
    waiting.run()
    assert waiting.snapshot()["reason"] == "Devin asked for input"
    assert fake.terminated == ["devin-test"]

    invalid_fake = FakeClient([{"state": "done", "detail": "exit", "structured_output": {}}])
    invalid = _job(tmp_path / "invalid", invalid_fake)
    invalid.cancel_event = ImmediateEvent()
    invalid.run()
    assert invalid.snapshot()["reason"] == "Devin returned an invalid plan"
    assert invalid_fake.terminated == ["devin-test"]

    missing_output_fake = FakeClient(
        [{"state": "done", "detail": "finished", "structured_output": None}]
    )
    missing_output = _job(tmp_path / "missing-output", missing_output_fake)
    missing_output.cancel_event = ImmediateEvent()
    missing_output.run()
    assert missing_output.snapshot()["reason"] == "Devin returned an invalid plan"
    assert missing_output_fake.terminated == ["devin-test"]

    store, _, _ = _stores(tmp_path / "http")
    invalid_url_plan = build_local_draft("teradata", "snowflake", store.current_path).model_dump()
    invalid_url_plan["sections"][0]["citations"] = [
        {"url": "http://insecure.example/doc", "note": "insecure"}
    ]
    invalid_url_fake = FakeClient(
        [{"state": "done", "detail": "exit", "structured_output": invalid_url_plan}]
    )
    invalid_url = _job(tmp_path / "invalid-url", invalid_url_fake)
    invalid_url.cancel_event = ImmediateEvent()
    invalid_url.run()
    assert invalid_url.snapshot()["reason"] == "Devin returned an invalid plan"
    assert invalid_url_fake.terminated == ["devin-test"]

    timeout_fake = FakeClient([])
    timeout = _job(tmp_path / "timeout", timeout_fake, timeout_minutes=0)
    timeout.cancel_event = ImmediateEvent()
    timeout.run()
    assert timeout.snapshot()["reason"] == "Devin session timed out"
    assert timeout_fake.terminated == ["devin-test"]


def test_plan_job_cancel_terminates_session(tmp_path: Path) -> None:
    created = threading.Event()
    fake = FakeClient([], created=created)
    job = _job(tmp_path, fake)
    job.start()
    assert created.wait(2)
    deadline = time.monotonic() + 2
    while job.session_id is None and time.monotonic() < deadline:
        time.sleep(0.001)
    job.cancel()
    job.join(2)
    assert fake.terminated == ["devin-test"]
    assert job.snapshot()["state"] == "cancelled"


def test_plan_job_cancel_during_session_creation_terminates_once(tmp_path: Path) -> None:
    created = threading.Event()
    release = threading.Event()
    fake = FakeClient([], created=created, create_release=release)
    job = _job(tmp_path, fake)
    job.start()
    assert created.wait(2)

    job.cancel()
    release.set()
    job.join(2)

    assert not job.is_alive()
    assert job.snapshot()["state"] == "cancelled"
    assert fake.terminated == ["devin-test"]


def test_plan_job_completes_blocked_v3_output(tmp_path: Path) -> None:
    store, _, _ = _stores(tmp_path)
    output = build_local_draft("teradata", "snowflake", store.current_path).model_dump()
    fake = FakeClient(
        [
            {
                "state": "blocked",
                "detail": "running; waiting_for_user",
                "structured_output": output,
            }
        ]
    )
    job = _job(tmp_path, fake)
    job.cancel_event = ImmediateEvent()
    job.run()
    assert job.snapshot()["state"] == "done"
    assert fake.terminated == ["devin-test"]


def test_plan_job_termination_failure_keeps_completed_plan(tmp_path: Path) -> None:
    store, _, _ = _stores(tmp_path)
    output = build_local_draft("teradata", "snowflake", store.current_path).model_dump()
    fake = FakeClient(
        [{"state": "done", "detail": "finished", "structured_output": output}],
        terminate_error=RuntimeError("sensitive remote error"),
    )
    job = _job(tmp_path, fake)
    job.cancel_event = ImmediateEvent()
    job.run()

    assert job.snapshot()["state"] == "done"
    records = [json.loads(line) for line in job.audit.path.read_text().splitlines()]
    failure = next(record for record in records if record["action"] == "plan.terminate_failed")
    assert failure["target"] == "devin-test"
    assert failure["details"] == {"error_type": "RuntimeError"}
    assert "sensitive remote error" not in job.audit.path.read_text()


def test_plan_job_selects_client_from_token_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import migraai.planning.job as job_module

    selected: list[str] = []
    monkeypatch.setattr(
        job_module,
        "DevinV1Client",
        lambda *_: selected.append("v1") or FakeClient([{"state": "failed", "detail": "test"}]),
    )
    monkeypatch.setattr(
        job_module,
        "DevinClient",
        lambda *_: selected.append("v3") or FakeClient([{"state": "failed", "detail": "test"}]),
    )
    v1 = _job(tmp_path / "v1", FakeClient([]), pat="apk_key", org_id="", client_factory=None)
    v1.cancel_event = ImmediateEvent()
    v1.run()
    v3 = _job(
        tmp_path / "v3",
        FakeClient([]),
        pat="cog_key",
        org_id="org-test",
        client_factory=None,
    )
    v3.cancel_event = ImmediateEvent()
    v3.run()
    assert selected == ["v1", "v3"]


def test_pdf_has_all_sections_and_escapes_raw_html(tmp_path: Path) -> None:
    store, plan_store, _ = _stores(tmp_path)
    plan = build_local_draft("teradata", "snowflake", store.current_path)
    plan.sections[0].content = "<script>alert(1)</script>"
    record = plan_store.save(
        plan,
        source="teradata",
        target="snowflake",
        kb_version=1,
        origin="local_draft",
        reason="Devin token not configured",
    )
    assert "<script>" not in render_markdown("<script>alert(1)</script>")
    logo = Path(__file__).parents[2] / "migraai/planning/assets/logo.png"
    content = render_plan_pdf(record, logo)
    assert content.startswith(b"%PDF")
    reader = PdfReader(__import__("io").BytesIO(content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    record["devin_session_id"] = "legacy-session-1"
    assert "Devin session: legacy-session-1" in "\n".join(
        page.extract_text() or ""
        for page in PdfReader(
            __import__("io").BytesIO(render_plan_pdf(record, logo_path=logo))
        ).pages
    )
    for _, title in SECTIONS:
        assert title in text


def test_pdf_maps_non_winansi_glyphs_without_changing_the_stored_plan(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store, plan_store, _ = _stores(tmp_path)
    plan = build_local_draft("teradata", "snowflake", store.current_path)
    plan.sections[0].content = "Teradata → Snowflake ← ≤ ≥ ≠ ✓ ✗ × – — “WinAnsi” •"
    record = plan_store.save(
        plan,
        source="teradata",
        target="snowflake",
        kb_version=1,
        origin="local_draft",
    )
    logo = Path(__file__).parents[2] / "migraai/planning/assets/logo.png"

    with caplog.at_level("WARNING"):
        content = render_plan_pdf(record, logo)

    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
    assert "Teradata -> Snowflake" in text
    assert "<-" in text
    assert "<=" in text
    assert ">=" in text
    assert "!=" in text
    assert "[x]" in text
    assert "[ ]" in text
    assert "x" in text
    assert "–" in text
    assert "—" in text
    assert "“WinAnsi”" in text
    assert "•" in text
    assert "→" not in text
    assert "×" not in text
    assert record["payload"]["sections"][0]["content"] == plan.sections[0].content
    assert "glyph" not in caplog.text.casefold()
    assert "→" not in caplog.text
