from __future__ import annotations

import json
import logging
import threading

import httpx
import pytest

from migraai.devin.client import DevinClient, DevinV1Client
from migraai.devin.runner import run_session

SCHEMA = {"type": "object", "properties": {"objects": {"type": "array"}}}
ATTACHMENT_URL = "https://files.example.test/batch.md"
OUTPUT = {"objects": [{"object_id": "sales.account"}]}


def _mock_client(version: str, scenario: str, cancel: threading.Event):
    state: dict[str, object] = {
        "creates": 0,
        "gets": 0,
        "terminates": 0,
        "attachment_body": b"",
        "create_payload": {},
        "create_payloads": [],
    }

    def respond(request: httpx.Request, status: int, payload: dict) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/attachments"):
            state["attachment_body"] = request.content
            return respond(request, 200, {"url": ATTACHMENT_URL})
        if request.method == "POST" and path.endswith("/sessions"):
            state["creates"] = int(state["creates"]) + 1
            state["create_payload"] = json.loads(request.content)
            state["create_payloads"].append(state["create_payload"])
            if scenario == "client_error":
                return respond(request, 422, {"detail": "invalid request"})
            if scenario == "retry" and state["creates"] == 1:
                return respond(request, 503, {"detail": "unavailable"})
            if scenario == "transport_retry" and state["creates"] == 1:
                raise httpx.ConnectError("connection reset", request=request)
            if scenario == "cancel":
                cancel.set()
            session_id = "devin-contract-session" if version == "v3" else "contract-session"
            return respond(
                request,
                200,
                {"session_id": session_id, "url": "https://devin.example.test/session"},
            )
        if request.method == "GET":
            state["gets"] = int(state["gets"]) + 1
            if scenario == "timeout":
                status, detail, output = "running", "", None
            elif scenario in {"blocked_with_output", "blocked_without_output"}:
                status = "running" if version == "v3" else "blocked"
                detail = "waiting_for_approval" if version == "v3" else ""
                output = OUTPUT if scenario == "blocked_with_output" else None
            else:
                status = "exit" if version == "v3" else "Finished"
                detail = "finished" if version == "v3" else ""
                output = OUTPUT
            payload = {
                "status": status,
                "structured_output": output,
            }
            if version == "v3":
                payload["status_detail"] = detail
                payload["acus_consumed"] = 3.75
            else:
                payload["status_enum"] = (
                    "blocked"
                    if scenario.startswith("blocked")
                    else "working"
                    if scenario == "timeout"
                    else "finished"
                )
            return respond(request, 200, payload)
        if request.method == "DELETE":
            state["terminates"] = int(state["terminates"]) + 1
            return httpx.Response(204, request=request)
        return respond(request, 404, {"detail": "not found"})

    transport = httpx.MockTransport(handler)
    if version == "v3":
        client = DevinClient(
            "https://api.example.test",
            "org-contract",
            "cog_test",
            transport=transport,
        )
    else:
        client = DevinV1Client("https://api.example.test", "apk_test", transport=transport)
    return client, state


@pytest.mark.parametrize("version", ["v1", "v3"])
@pytest.mark.parametrize(
    ("scenario", "expected_state"),
    [
        ("success", "done"),
        ("client_error", "failed"),
        ("retry", "done"),
        ("transport_retry", "done"),
        ("timeout", "timeout"),
        ("cancel", "cancelled"),
        ("blocked_with_output", "done"),
        ("blocked_without_output", "blocked"),
    ],
)
def test_v1_v3_runner_contract(
    version: str,
    scenario: str,
    expected_state: str,
) -> None:
    cancel = threading.Event()
    client, state = _mock_client(version, scenario, cancel)
    outcome = run_session(
        client,
        prompt="Convert this object.",
        attachment_name="batch.md",
        attachment_md="SENSITIVE_ATTACHMENT_TEXT",
        schema=SCHEMA,
        title="Conversion",
        tags=["migraai", "voter-A"],
        timeout_s=0.01,
        cancel=cancel,
        poll_initial=0,
        poll_max=0.01,
    )
    client.close()

    assert outcome.state == expected_state
    assert "max_acu_limit" not in state["create_payload"]
    assert b"SENSITIVE_ATTACHMENT_TEXT" in state["attachment_body"]
    assert int(state["terminates"]) == (0 if scenario == "client_error" else 1)
    if scenario == "success":
        assert outcome.structured_output == OUTPUT
        assert outcome.acus == (3.75 if version == "v3" else None)
        create_payload = state["create_payload"]
        assert 'ATTACHMENT:"https://files.example.test/batch.md"' in create_payload["prompt"]
        if version == "v3":
            assert create_payload["attachment_urls"] == [ATTACHMENT_URL]
    if scenario in {"retry", "transport_retry"}:
        assert int(state["creates"]) == 2
    if scenario in {"blocked_with_output", "blocked_without_output", "timeout", "cancel"}:
        assert outcome.session_id is not None
    if scenario == "blocked_with_output":
        assert outcome.structured_output == OUTPUT
    if scenario == "blocked_without_output":
        assert outcome.structured_output is None


@pytest.mark.parametrize("version", ["v1", "v3"])
def test_runner_logs_never_include_prompt_or_attachment(
    version: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    cancel = threading.Event()
    client, _ = _mock_client(version, "success", cancel)
    run_session(
        client,
        prompt="SENSITIVE_PROMPT_TEXT",
        attachment_name="batch.md",
        attachment_md="SENSITIVE_ATTACHMENT_TEXT",
        schema=SCHEMA,
        title="Conversion",
        tags=["migraai"],
        timeout_s=1,
        cancel=cancel,
        poll_initial=0,
    )
    client.close()
    assert "SENSITIVE_PROMPT_TEXT" not in caplog.text
    assert "SENSITIVE_ATTACHMENT_TEXT" not in caplog.text


@pytest.mark.parametrize("version", ["v1", "v3"])
@pytest.mark.parametrize("scenario, expected_creates", [("success", 1), ("retry", 2)])
def test_runner_forwards_acu_limit_on_each_create_attempt(
    version: str,
    scenario: str,
    expected_creates: int,
) -> None:
    cancel = threading.Event()
    client, state = _mock_client(version, scenario, cancel)
    outcome = run_session(
        client,
        prompt="Convert this object.",
        attachment_name="batch.md",
        attachment_md="",
        schema=SCHEMA,
        title="Conversion",
        tags=["migraai", "voter-A"],
        max_acu_limit=7,
        timeout_s=1,
        cancel=cancel,
        poll_initial=0,
    )
    client.close()

    assert outcome.state == "done"
    assert len(state["create_payloads"]) == expected_creates
    assert all(payload["max_acu_limit"] == 7 for payload in state["create_payloads"])
