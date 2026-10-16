from __future__ import annotations

import threading
from pathlib import Path

from migraai.devin.attachment import parse_batch, render_batch
from migraai.devin.client import build_devin_client
from migraai.devin.mock import MockDevinClient, MockScenario
from migraai.devin.runner import run_session


def _run_mock(
    scenario: MockScenario | None = None,
    voter: str = "A",
    *,
    timeout_s: float = 0.02,
) -> tuple[MockDevinClient, object]:
    client = MockDevinClient(
        scenario,
        source_dialect="teradata",
        target_dialect="snowflake",
    )
    attachment = render_batch(
        [
            {
                "object_id": "sales.account",
                "rel_path": "tables/account.sql",
                "language": "sql",
                "source_text": "SELECT TOP 10 account_id FROM sales.account;",
                "summary": "Account table query",
            }
        ]
    )
    outcome = run_session(
        client,
        prompt="Convert from Teradata to Snowflake.",
        attachment_name="batch.md",
        attachment_md=attachment,
        schema={"type": "object"},
        title="Conversion",
        tags=["migraai", f"voter-{voter}"],
        timeout_s=timeout_s,
        cancel=threading.Event(),
        poll_initial=0,
        poll_max=0.01,
    )
    return client, outcome


def test_attachment_render_parse_round_trip_with_fenced_source() -> None:
    objects = [
        {
            "object_id": "schema.function",
            "rel_path": "functions/example.py",
            "language": "python",
            "source_text": "def example():\n    return ```\n",
            "summary": "A function",
            "dependencies": ["schema.table"],
        }
    ]
    assert parse_batch(render_batch(objects)) == [
        {
            "object_id": "schema.function",
            "rel_path": "functions/example.py",
            "language": "python",
            "summary": "A function",
            "dependencies": ["schema.table"],
            "source_text": "def example():\n    return ```\n",
        }
    ]


def test_mock_transpiles_and_wraps_source_with_comment_markers() -> None:
    client, outcome = _run_mock()
    assert outcome.state == "done"
    file_output = outcome.structured_output["objects"][0]["files"][0]
    assert file_output["content"].startswith(
        "-- Converted: Generated a deterministic snowflake draft.\n"
        "-- Reason: Preserved the source text using the deterministic mock conversion.\n"
    )
    assert "LIMIT 10" in file_output["content"]
    assert client.termination_calls == [outcome.session_id]


def test_mock_per_voter_failure_timeout_and_blocked_scenarios() -> None:
    failed_client, failed = _run_mock(MockScenario(fail_voters={"A"}))
    assert failed.state == "failed"
    assert failed_client.termination_calls == [failed.session_id]

    timed_out_client, timed_out = _run_mock(MockScenario(timeout_voters={"B"}), "B")
    assert timed_out.state == "timeout"
    assert timed_out_client.termination_calls == [timed_out.session_id]

    blocked_client, blocked = _run_mock(MockScenario(blocked_voters={"C"}), "C")
    assert blocked.state == "blocked"
    assert blocked_client.termination_calls == [blocked.session_id]

    output_client, blocked_with_output = _run_mock(
        MockScenario(blocked_with_output_voters={"D"}),
        "D",
    )
    assert blocked_with_output.state == "done"
    assert blocked_with_output.structured_output is not None
    assert output_client.termination_calls == [blocked_with_output.session_id]


def test_mock_can_omit_object_ids_and_diverge_per_stance() -> None:
    missing_client, missing = _run_mock(MockScenario(missing_object_ids={"A": {"sales.account"}}))
    assert missing.structured_output["objects"] == []

    scenario = MockScenario(different_candidate_per_stance=True)
    client_a, candidate_a = _run_mock(scenario, "A")
    client_b, candidate_b = _run_mock(scenario, "B")
    assert candidate_a.structured_output != candidate_b.structured_output
    assert "Candidate: A" in candidate_a.structured_output["objects"][0]["files"][0]["content"]
    assert "Candidate: B" in candidate_b.structured_output["objects"][0]["files"][0]["content"]
    assert (
        candidate_a.structured_output["objects"][0]["files"][0]["rel_path"]
        == candidate_b.structured_output["objects"][0]["files"][0]["rel_path"]
    )
    assert missing_client.termination_calls == [missing.session_id]
    assert client_a.termination_calls == [candidate_a.session_id]
    assert client_b.termination_calls == [candidate_b.session_id]


def test_mock_retries_one_create_5xx() -> None:
    client, outcome = _run_mock(MockScenario(create_5xx_once=True))
    assert outcome.state == "done"
    assert client.termination_calls == [outcome.session_id]


def test_mock_client_is_selected_only_with_explicit_test_flag(monkeypatch) -> None:
    monkeypatch.delenv("MIGRAAI_TEST_MOCK_DEVIN", raising=False)
    assert build_devin_client("https://api.example.test", "", "") is None

    monkeypatch.setenv("MIGRAAI_TEST_MOCK_DEVIN", "1")
    monkeypatch.setenv("MIGRAAI_TEST_MOCK_DEVIN_DIFFERENT_CANDIDATE", "1")
    client = build_devin_client("https://api.example.test", "", "")
    assert isinstance(client, MockDevinClient)
    assert client.scenario.different_candidate_per_stance
    client.close()


def test_mock_falls_back_to_source_text_when_transpilation_fails(monkeypatch) -> None:
    def fail_transpile(*args, **kwargs):
        raise ValueError("invalid mock source")

    monkeypatch.setattr("migraai.devin.mock.sqlglot.transpile", fail_transpile)
    source = "SELECT FROM"
    assert MockDevinClient._transpile(source, "sql", "teradata", "snowflake") == source


def test_portable_launcher_generator_does_not_enable_mock_devin() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    build_script = repo_root / "packaging" / "windows" / "build_portable.py"
    source = build_script.read_text(encoding="utf-8")
    launcher_template = source.split('(APP_DIR / "start.cmd").write_text(', 1)[1].split(
        '(APP_DIR / "README.txt")', 1
    )[0]
    assert "MIGRAAI_TEST_MOCK_DEVIN" not in launcher_template
