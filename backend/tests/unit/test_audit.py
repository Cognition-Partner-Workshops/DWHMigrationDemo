from __future__ import annotations

import json
from pathlib import Path

from migraai.core.audit import AuditLog, verify_chain


def test_audit_chain_verifies_and_resumes_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "audit.jsonl"
    first = AuditLog(path)
    first.append("user", "session.confirm", "session-1", {"source": "teradata"})
    first.append("system", "app.stop", "application")

    assert verify_chain(path) == (True, None)

    reopened = AuditLog(path)
    record = reopened.append("system", "app.start", "application")
    assert record["seq"] == 3
    assert verify_chain(path) == (True, None)


def test_audit_chain_detects_an_edited_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path)
    audit.append("user", "settings.update", "app-settings", {"fields": ["devin_org_id"]})
    audit.append("user", "session.confirm", "session-1")

    lines = path.read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[0])
    edited["details"]["fields"] = ["devin_pat"]
    lines[0] = json.dumps(edited)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert verify_chain(path) == (False, 1)


def test_empty_audit_file_verifies(tmp_path: Path) -> None:
    assert verify_chain(tmp_path / "missing.jsonl") == (True, None)
