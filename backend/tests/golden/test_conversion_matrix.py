from __future__ import annotations

import difflib
import gc
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from migraai.agents import workflow
from migraai.agents.job import ConversionJob
from migraai.core.audit import AuditLog
from migraai.core.crypto import EncryptedFileStore, SessionCipher
from migraai.devin.mock import MockDevinClient
from migraai.discovery.detect import decode_source
from migraai.discovery.inventory import Inventory
from migraai.discovery.pipeline import discover_files
from migraai.discovery.uploads import UploadRow
from migraai.packaging.model import PackageInput
from migraai.packaging.parts import collect_parts
from migraai.packaging.snapshot import build_package_input
from migraai.packaging.zipper import build_zip
from migraai.registry.platforms import SOURCES, TARGETS

ROOT = Path(__file__).resolve().parents[3]
TESTPACKS = ROOT / "testpacks"
FIXED_TIMESTAMP = "2026-01-01T00:00:00Z"
CHECK_NAMES = (
    "syntax",
    "lint",
    "semantic",
    "rule",
    "dependency",
    "object_reference",
    "security",
)
SECRET_LIKE = re.compile(
    r"\b(?:apk|pat|dvn|gh[pousr])_[A-Za-z0-9_-]{12,}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
    r"|\bsk-[A-Za-z0-9_-]{20,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b",
    re.IGNORECASE,
)
ABSOLUTE_PATH = re.compile(r"(?<![\w.])/(?:home|tmp)/[A-Za-z0-9_./-]+")
WINDOWS_PATH = re.compile(r"\b[A-Z]:\\[^\\/\s\"'<>|?*]+(?:\\[^\\/\s\"'<>|?*]+)*", re.IGNORECASE)

SOURCE_IDS = [str(item["id"]) for item in SOURCES]
TARGET_IDS = [str(item["id"]) for item in TARGETS]
ALL_PAIRS = [(source, target) for source in SOURCE_IDS for target in TARGET_IDS]
SMOKE_PAIRS = [
    (source, TARGET_IDS[index % len(TARGET_IDS)]) for index, source in enumerate(SOURCE_IDS)
]
PAIRS = ALL_PAIRS if os.environ.get("MIGRAAI_GOLDEN_ALL") == "1" else SMOKE_PAIRS
if source_filter := os.environ.get("MIGRAAI_GOLDEN_SOURCE"):
    PAIRS = [(source, target) for source, target in PAIRS if source == source_filter]
if target_filter := os.environ.get("MIGRAAI_GOLDEN_TARGET"):
    PAIRS = [(source, target) for source, target in PAIRS if target == target_filter]
PAIR_PARAMS = [pytest.param(source, target, id=f"{source}-to-{target}") for source, target in PAIRS]


def _source_files(pack_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(pack_dir).as_posix(): path.read_bytes()
        .replace(b"\r\n", b"\n")
        .replace(b"\r", b"\n")
        for path in sorted(pack_dir.rglob("*.sql"))
        if path.is_file()
    }


def _session_for_pack(pack_dir: Path, source: str, target: str, tmp_path: Path) -> dict[str, Any]:
    files = _source_files(pack_dir)
    inventory = Inventory()
    store = EncryptedFileStore(tmp_path / "session", SessionCipher())
    uploads: dict[str, UploadRow] = {}
    previews: dict[str, str] = {}
    for rel_path, content in files.items():
        row_id = uuid.uuid4().hex
        store_path = f"uploads/{row_id}"
        store.write(store_path, content)
        row = UploadRow(
            id=row_id,
            rel_path=rel_path,
            size=len(content),
            detected_type="sql",
            status="done",
            store_path=store_path,
        )
        inventory.add_upload(row)
        uploads[row.id] = row
        previews[rel_path] = decode_source(content)[0]
    objects, edges = discover_files(files, source)
    inventory.replace_discovery(objects, edges, uploads, previews)
    return {
        "session_id": "golden-session",
        "source": source,
        "target": target,
        "inventory": inventory,
        "store": store,
        "uploads": uploads,
    }


def _release_session(session: dict[str, Any]) -> None:
    session["inventory"].close()
    gc.collect()


def _normalise(package: PackageInput, source: str, target: str) -> PackageInput:
    object_ids = {
        item.object_id: f"golden-object-{index:04d}" for index, item in enumerate(package.objects)
    }
    objects = tuple(
        replace(
            item,
            object_id=object_ids[item.object_id],
            dependencies=tuple(object_ids.get(value, value) for value in item.dependencies),
        )
        for item in package.objects
    )
    sessions = tuple(
        replace(
            item,
            url=f"mock://golden/session-{index:04d}" if item.url else None,
            run_id=f"golden-{source}-{target}",
        )
        for index, item in enumerate(package.sessions)
    )
    egress = replace(package.egress, consent_at=FIXED_TIMESTAMP) if package.egress else None
    return replace(
        package,
        run_id=f"golden-{source}-{target}",
        created_at=FIXED_TIMESTAMP,
        objects=objects,
        sessions=sessions,
        egress=egress,
    )


def _entry_texts(archive_bytes: bytes) -> list[str]:
    texts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in archive.namelist():
            content = archive.read(name)
            nested = io.BytesIO(content)
            if zipfile.is_zipfile(nested):
                texts.extend(_entry_texts(content))
            elif name.lower().endswith(".pdf"):
                reader = PdfReader(io.BytesIO(content))
                texts.append("\n".join(page.extract_text() or "" for page in reader.pages))
            else:
                texts.append(content.decode("utf-8", errors="ignore"))
    return texts


def _assert_safe(
    archive_bytes: bytes,
    source_files: dict[str, bytes],
) -> None:
    source_text = "\n".join(
        content.decode("utf-8", errors="ignore") for content in source_files.values()
    )
    allowed_paths = {
        match.rstrip(".,;:)]}")
        for match in ABSOLUTE_PATH.findall(source_text)
        if match.startswith("/tmp/")
    }
    allowed_windows_paths = {match.rstrip(".,;:)]}") for match in WINDOWS_PATH.findall(source_text)}
    contents = "\n".join(_entry_texts(archive_bytes))
    assert "demo-secret" not in contents
    assert not SECRET_LIKE.search(contents)
    for match in ABSOLUTE_PATH.findall(contents):
        path = match.rstrip(".,;:)]}")
        assert path != "/home" and not path.startswith(("/home/", "/tmp/pytest"))
        if path.startswith("/tmp/"):
            assert path in allowed_paths, f"Unallowlisted absolute path in package: {path}"
    for match in WINDOWS_PATH.findall(contents):
        path = match.rstrip(".,;:)]}")
        assert path in allowed_windows_paths, f"Unallowlisted absolute path in package: {path}"


def _golden_result(
    package: PackageInput,
    archive_bytes: bytes,
    source: str,
    target: str,
) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        names = sorted(archive.namelist())
        manifest = json.loads(archive.read(f"{package.package_name}-converted/manifest.json"))
    status_counts = Counter(item.status for item in package.objects)
    return {
        "inventory": {
            "objects": len(package.objects) + len(package.external),
            "external": len(package.external),
        },
        "conversion": {
            "status_counts": {
                status: status_counts.get(status, 0)
                for status in ("converted", "expert_review", "failed", "cancelled")
            },
            "objects": [
                {
                    "name": item.source_qualified_name,
                    "type": item.object_type,
                    "checks": {
                        check: next(
                            (summary.status for summary in item.checks if summary.check == check),
                            "not_applicable",
                        )
                        for check in CHECK_NAMES
                    },
                }
                for item in sorted(
                    package.objects,
                    key=lambda value: (
                        value.source_qualified_name,
                        value.object_type,
                        value.source_file,
                        value.source_line_start,
                    ),
                )
            ],
        },
        "zip_entries": names,
        "manifest_counts": manifest["counts"],
        "zip_sha256": hashlib.sha256(archive_bytes).hexdigest(),
    }


@pytest.mark.parametrize(("source", "target"), PAIR_PARAMS)
def test_mock_conversion_matches_golden(
    source: str,
    target: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    pack_dir = TESTPACKS / source
    source_files = _source_files(pack_dir)
    session = _session_for_pack(pack_dir, source, target, tmp_path)
    request.addfinalizer(lambda: _release_session(session))
    original_run_session = workflow.run_session

    def fast_mock_poll(client: Any, **kwargs: Any) -> Any:
        kwargs["poll_initial"] = 0
        kwargs["poll_max"] = 0.1
        return original_run_session(client, **kwargs)

    monkeypatch.setattr(workflow, "run_session", fast_mock_poll)
    job = ConversionJob(
        session=session,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        client_factory=lambda _role: MockDevinClient(),
        batch_size=20,
        max_concurrent_sessions=4,
        devin_host="local mock",
    )
    job.run()
    assert job.state == "done", f"{source} to {target} job ended {job.state}: {job.reason}"
    package = _normalise(build_package_input(job, session, ""), source, target)
    first_parts = collect_parts(package)
    first_zip = build_zip(package, first_parts)
    second_parts = collect_parts(package)
    second_zip = build_zip(package, second_parts)
    assert first_zip == second_zip, f"{source} to {target} produced nondeterministic ZIP bytes"
    _assert_safe(first_zip, source_files)
    actual = _golden_result(package, first_zip, source, target)
    expected_path = pack_dir / "golden" / f"{target}.json"
    rendered = json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(rendered, encoding="utf-8", newline="\n")
        return
    assert expected_path.is_file(), f"Missing golden file: {expected_path}"
    expected = expected_path.read_text(encoding="utf-8")
    if expected != rendered:
        diff = difflib.unified_diff(
            expected.splitlines(),
            rendered.splitlines(),
            fromfile=str(expected_path),
            tofile="actual",
            lineterm="",
        )
        pytest.fail("Golden mismatch:\n" + "\n".join(diff))
