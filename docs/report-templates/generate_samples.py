from __future__ import annotations

import argparse
import gc
import importlib.util
import io
import os
import re
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

from pypdf import PdfReader

import migraai.main as migraai_main
from migraai.agents import workflow
from migraai.agents.job import ConversionJob
from migraai.core.audit import AuditLog
from migraai.devin.mock import MockDevinClient
from migraai.kb.store import KBStore
from migraai.packaging.parts import collect_parts
from migraai.packaging.snapshot import build_package_input
from migraai.packaging.zipper import build_zip
from migraai.planning.pdf import render_plan_pdf
from migraai.planning.store import PlanStore

ROOT = Path(__file__).resolve().parents[2]
REGENERATION_COMMAND = (
    "uv run --project backend python docs/report-templates/generate_samples.py "
    "--out docs/report-templates/samples"
)
SAMPLE_FILES = (
    "session-report.md",
    "session-report.pdf",
    "mapping-workbook.xlsx",
    "manifest.json",
    "migration-plan.pdf",
)


def _load_golden_helpers() -> ModuleType:
    path = ROOT / "backend" / "tests" / "golden" / "test_conversion_matrix.py"
    spec = importlib.util.spec_from_file_location("report_samples_golden_conversion_matrix", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load golden conversion helpers from {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _restore_environment(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _render_plan(kb_store: KBStore, tmp_dir: Path, inventory: Any, logo: Path) -> bytes:
    from migraai.planning.local_draft import build_local_draft

    with inventory.lock:
        aggregates = inventory.aggregates()
        fingerprint = inventory.fingerprint()
    if not fingerprint:
        aggregates = None
    if kb_store.current_path is None:
        raise RuntimeError("Knowledge base did not initialize a current database.")
    plan = build_local_draft(
        "teradata",
        "snowflake",
        kb_store.current_path,
        inventory_aggregates=aggregates,
    )
    plan_store = PlanStore(tmp_dir / "data" / "app.db")
    plan_store.initialize()
    record = plan_store.save(
        plan,
        source="teradata",
        target="snowflake",
        kb_version=int(kb_store.current_version or 1),
        origin="local_draft",
        reason="Devin token not configured",
        inventory_fingerprint=fingerprint,
    )
    record["created_at"] = "2026-01-01T00:00:00+00:00"

    import reportlab.rl_config

    previous_invariant = reportlab.rl_config.invariant
    reportlab.rl_config.invariant = 1
    try:
        return render_plan_pdf(record, logo)
    finally:
        reportlab.rl_config.invariant = previous_invariant


def _extracted_text(name: str, content: bytes) -> list[str]:
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content))
        return ["\n".join(page.extract_text() or "" for page in reader.pages)]
    if name.endswith(".xlsx"):
        with zipfile.ZipFile(io.BytesIO(content)) as workbook:
            return [
                workbook.read(member).decode("utf-8", errors="ignore")
                for member in workbook.namelist()
                if member.endswith(".xml")
            ]
    return [content.decode("utf-8", errors="ignore")]


def _assert_no_leaks(outputs: dict[str, bytes], temp_dir: Path, golden: ModuleType) -> None:
    forbidden_paths = {
        str(temp_dir),
        temp_dir.as_posix(),
        str(ROOT),
        ROOT.as_posix(),
    }
    drive_path = re.compile(r"\b[A-Z]:\\", re.IGNORECASE)
    for name, content in outputs.items():
        raw_text = content.decode("latin-1")
        text_candidates = _extracted_text(name, content)
        candidates = [raw_text, *text_candidates]
        for candidate in candidates:
            for path in forbidden_paths:
                if path and path in candidate:
                    raise ValueError(f"Unsafe {name}: contains local path {path!r}.")
            for token in ("/home/", "/tmp/"):
                if token in candidate:
                    raise ValueError(f"Unsafe {name}: contains forbidden path prefix {token!r}.")
            if "demo-secret" in candidate:
                raise ValueError(f"Unsafe {name}: contains forbidden text 'demo-secret'.")
            if golden.SECRET_LIKE.search(candidate):
                raise ValueError(f"Unsafe {name}: contains secret-like text.")
        drive_candidates = text_candidates if name.endswith((".pdf", ".xlsx")) else candidates
        for candidate in drive_candidates:
            drive_match = drive_path.search(candidate)
            if drive_match:
                raise ValueError(
                    f"Unsafe {name}: contains a Windows drive path {drive_match.group(0)!r}."
                )


def generate(out_dir: Path) -> dict[str, Path]:
    previous_environment = {
        "MIGRAAI_DATA_DIR": os.environ.get("MIGRAAI_DATA_DIR"),
        "MIGRAAI_EMBEDDER": os.environ.get("MIGRAAI_EMBEDDER"),
    }
    os.environ["MIGRAAI_EMBEDDER"] = "hash"
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            tmp_dir = Path(temp)
            data_dir = tmp_dir / "data"
            os.environ["MIGRAAI_DATA_DIR"] = str(data_dir)

            golden = _load_golden_helpers()
            audit = AuditLog(data_dir / "logs" / "audit.jsonl")
            kb_store = KBStore(data_dir)
            build_database = migraai_main.build_database

            def build_database_and_collect(*args: Any, **kwargs: Any) -> Path:
                database = build_database(*args, **kwargs)
                gc.collect()
                return database

            with patch.object(migraai_main, "build_database", build_database_and_collect):
                seed_error = migraai_main._initialize_kb(
                    kb_store,
                    ROOT / "backend" / "migraai" / "kb" / "seed",
                    tmp_dir / "no-bundled-kb.db",
                    audit,
                )
            if seed_error is not None:
                raise RuntimeError(
                    f"Knowledge-base initialization reported seed error {seed_error}."
                )

            pack_dir = ROOT / "testpacks" / "teradata"
            session = golden._session_for_pack(pack_dir, "teradata", "snowflake", tmp_dir)
            try:
                original_run_session = workflow.run_session

                def fast_mock_poll(client: Any, **kwargs: Any) -> Any:
                    kwargs["poll_initial"] = 0
                    kwargs["poll_max"] = 0.1
                    return original_run_session(client, **kwargs)

                workflow.run_session = fast_mock_poll
                try:
                    job = ConversionJob(
                        session=session,
                        audit=AuditLog(tmp_dir / "audit.jsonl"),
                        client_factory=lambda _role: MockDevinClient(),
                        batch_size=20,
                        max_concurrent_sessions=4,
                        devin_host="local mock",
                    )
                    job.run()
                finally:
                    workflow.run_session = original_run_session

                if job.state != "done":
                    raise RuntimeError(f"teradata to snowflake job ended {job.state}: {job.reason}")
                package = golden._normalise(
                    build_package_input(job, session, ""),
                    "teradata",
                    "snowflake",
                )
                sample_run_id = "sample-teradata-snowflake"
                package = replace(
                    package,
                    run_id=sample_run_id,
                    sessions=tuple(
                        replace(item, run_id=sample_run_id) for item in package.sessions
                    ),
                )
                parts = collect_parts(package)
                archive = build_zip(package, parts)
                golden._assert_safe(archive, golden._source_files(pack_dir))

                with zipfile.ZipFile(io.BytesIO(archive)) as package_zip:
                    package_root = f"{package.package_name}-converted/"
                    archive_outputs = {
                        "session-report.md": "report/session-report.md",
                        "session-report.pdf": "report/session-report.pdf",
                        "mapping-workbook.xlsx": "mapping/mapping-workbook.xlsx",
                        "manifest.json": "manifest.json",
                    }
                    outputs = {
                        name: package_zip.read(package_root + archive_path)
                        for name, archive_path in archive_outputs.items()
                    }
                logo = ROOT / "backend" / "migraai" / "planning" / "assets" / "logo.png"
                outputs["migration-plan.pdf"] = _render_plan(
                    kb_store, tmp_dir, session["inventory"], logo
                )
                _assert_no_leaks(outputs, tmp_dir, golden)

                output_dir = Path(out_dir)
                output_dir.mkdir(parents=True, exist_ok=True)
                result = {}
                for name in SAMPLE_FILES:
                    path = output_dir / name
                    path.write_bytes(outputs[name])
                    result[name] = path
                return result
            finally:
                golden._release_session(session)
    finally:
        _restore_environment(previous_environment)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic report-template samples.")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "docs" / "report-templates" / "samples",
        help="Output directory (default: docs/report-templates/samples).",
    )
    args = parser.parse_args(argv)
    generate(args.out)


if __name__ == "__main__":
    main()
