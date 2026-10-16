from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from migraai.agents.context import (
    _find_parsed,
    _node_summary,
    _parsed_objects,
    _source_for,
    get_pair_mappings,
    get_relevant_mappings,
)
from migraai.agents.job import ConversionJob
from migraai.core import secret_mask
from migraai.packaging.model import (
    CheckSummary,
    EgressSummary,
    FindingSummary,
    MappingRef,
    PackagedFile,
    PackagedObject,
    PackageInput,
    SessionSummary,
)
from migraai.packaging.naming import converted_path, upload_root
from migraai.packaging.naming import package_name as normalize_package_name
from migraai.validation.dialects import sqlglot_dialect
from migraai.validation.sql import exclude_snowflake_stage_copy_statements
from migraai.validation.validate import pass_rate

_STATUSES = {"converted", "expert_review", "failed", "cancelled"}
_LANGUAGES = {"sql", "python", "json", "yaml"}


def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _inventory_rows(session: dict[str, Any]) -> list[dict[str, Any]]:
    inventory = session["inventory"]
    with inventory.lock:
        return [
            dict(row) for row in inventory.connection.execute("SELECT * FROM object ORDER BY rowid")
        ]


def _conversion_rows(
    session: dict[str, Any], run_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    inventory = session["inventory"]
    with inventory.lock:
        run = inventory.connection.execute(
            "SELECT * FROM conversion_run WHERE id=?", (run_id,)
        ).fetchone()
        objects = [
            dict(row)
            for row in inventory.connection.execute(
                "SELECT * FROM conversion_object WHERE run_id=?", (run_id,)
            )
        ]
        sessions = [
            dict(row)
            for row in inventory.connection.execute(
                "SELECT * FROM conversion_session WHERE run_id=? ORDER BY rowid",
                (run_id,),
            )
        ]
    return (dict(run) if run is not None else {}), objects + sessions


def _created_at(value: Any, consent_at: str | None) -> str:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        except ValueError:
            pass
    if isinstance(value, (int, float)):
        return (
            datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
    return consent_at or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _audit_records(job: Any) -> list[dict[str, Any]]:
    path = getattr(getattr(job, "audit", None), "path", None)
    if not isinstance(path, Path) or not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("target") == job.run_id:
            records.append(record)
    return records


def _utc_time(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _check_summaries(checks: list[Any]) -> tuple[CheckSummary, ...]:
    summaries = []
    for check in checks:
        findings = tuple(
            FindingSummary(
                finding.check,
                finding.rule_id,
                finding.severity,
                secret_mask.mask_secrets(finding.message),
                finding.line,
            )
            for finding in getattr(check, "findings", [])
        )
        summaries.append(
            CheckSummary(
                check.check,
                check.status,
                findings,
            )
        )
    return tuple(summaries)


def _target_column_types(files: list[dict[str, Any]], target: str) -> dict[str, str]:
    columns: dict[str, str] = {}
    dialect = sqlglot_dialect(target)
    for file in files:
        if str(file.get("language", "")).casefold() != "sql":
            continue
        try:
            content = exclude_snowflake_stage_copy_statements(str(file.get("content", "")))
            statements = sqlglot.parse(content, read=dialect)
        except (ParseError, TokenError):
            continue
        for statement in statements:
            if statement is None:
                continue
            for column in statement.find_all(exp.ColumnDef):
                data_type = column.args.get("kind")
                if not isinstance(data_type, exp.DataType):
                    continue
                name = column.this.name
                if name:
                    columns[name.casefold()] = data_type.sql(dialect=dialect)
    return columns


def _relevant_pair_mappings(
    source: str,
    target: str,
    source_text: str,
    target_text: str,
    db_path: Any,
) -> list[MappingRef]:
    combined = f"{source_text}\n{target_text}".casefold()
    platform_names = {source.casefold(), target.casefold()}
    refs = []
    for mapping in get_pair_mappings(source, target, db_path):
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", mapping["note"])
        relevant_words = [
            word for word in words if word.casefold() not in platform_names and len(word) > 2
        ]
        if not relevant_words or not any(word.casefold() in combined for word in relevant_words):
            continue
        refs.append(
            MappingRef(
                "syntax",
                secret_mask.mask_secrets(mapping["source"]),
                secret_mask.mask_secrets(mapping["target"]),
                secret_mask.mask_secrets(mapping["note"]),
                mapping["kb_ref"] or None,
            )
        )
    return refs


def _mapping_refs(
    job: Any,
    source_row: dict[str, Any],
    source_text: str,
    target_files: list[dict[str, Any]],
    source_columns: list[dict[str, Any]],
    checks: list[Any],
    db_path: Any,
) -> tuple[MappingRef, ...]:
    source_name = str(source_row.get("qualified_name", ""))
    target_name = job.name_map.get(source_name, source_name)
    refs = [MappingRef("object", source_name, target_name)]

    lookups = get_relevant_mappings(job.source, job.target, [source_text], db_path)
    type_lookups = [item for item in lookups if item["kind"] == "type"]
    target_types = _target_column_types(target_files, job.target)
    for column in source_columns:
        source_type = str(column.get("type", "")).strip()
        target_type = target_types.get(str(column.get("name", "")).casefold())
        if not source_type or not target_type:
            continue
        base_type = re.sub(r"\s*\(.*$", "", source_type).casefold()
        mapped = next(
            (
                item
                for item in type_lookups
                if re.sub(r"\s*\(.*$", "", item["source"]).casefold() == base_type
            ),
            None,
        )
        refs.append(
            MappingRef(
                "data_type",
                secret_mask.mask_secrets(source_type),
                secret_mask.mask_secrets(target_type),
                secret_mask.mask_secrets(mapped["note"]) if mapped else "",
                (mapped["kb_ref"] or None) if mapped else None,
            )
        )

    for mapping in lookups:
        if mapping["kind"] == "function":
            refs.append(
                MappingRef(
                    "function",
                    secret_mask.mask_secrets(mapping["source"]),
                    secret_mask.mask_secrets(mapping["target"]),
                    secret_mask.mask_secrets(mapping["note"]),
                    mapping["kb_ref"] or None,
                )
            )

    target_text = "\n".join(
        secret_mask.mask_secrets(str(file.get("content", ""))) for file in target_files
    )
    refs.extend(_relevant_pair_mappings(job.source, job.target, source_text, target_text, db_path))

    rules = {rule.id: rule for rule in getattr(job.pack, "rules", [])}
    for check in checks:
        for finding in getattr(check, "findings", []):
            if finding.check not in {"rule", "security"}:
                continue
            rule = rules.get(finding.rule_id)
            claim = getattr(rule, "claim", None)
            refs.append(
                MappingRef(
                    "security" if finding.check == "security" else "syntax",
                    secret_mask.mask_secrets(finding.rule_id),
                    secret_mask.mask_secrets(getattr(rule, "replacement", None) or finding.message),
                    secret_mask.mask_secrets(getattr(claim, "text", "") or finding.message),
                    getattr(claim, "url", None),
                )
            )
    return tuple(dict.fromkeys(refs))


def _job_history(job: Any) -> list[Any]:
    history: list[Any] = []
    seen: set[str] = set()
    current = job
    while current is not None and str(current.run_id) not in seen:
        seen.add(str(current.run_id))
        history.append(current)
        current = getattr(current, "base", None)
    return list(reversed(history))


def _session_summaries(
    jobs: list[Any],
    sessions: list[dict[str, Any]],
) -> tuple[SessionSummary, ...]:
    jobs_by_run = {str(item.run_id): item for item in jobs}
    batch_by_run = {
        run_id: {
            str(_value(item, "id", "")): batch.index
            for batch in job.batches
            for item in batch.objects
        }
        for run_id, job in jobs_by_run.items()
    }
    role_names = {"A": "voter_a", "B": "voter_b", "C": "voter_c", "escalation": "escalation"}
    grouped: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    known_session_ids = {
        (str(row.get("run_id", "")), str(row.get("session_id")))
        for row in sessions
        if row.get("session_id")
    }
    for row in sessions:
        if "role" not in row:
            continue
        role = role_names.get(str(row["role"]))
        if role is None:
            continue
        run_id = str(row.get("run_id", ""))
        batch_index = (
            int(row["batch_index"])
            if row.get("batch_index") is not None
            else batch_by_run.get(run_id, {}).get(str(row.get("object_id", "")), 0)
        )
        session_id = str(row.get("session_id") or "")
        key = (run_id, role, batch_index, session_id or str(row.get("state", "")))
        grouped.setdefault(key, row)
    for job in jobs:
        run_id = str(job.run_id)
        for session_id, info in getattr(job, "started_sessions", {}).items():
            if (run_id, str(session_id)) in known_session_ids or not isinstance(info, dict):
                continue
            role = role_names.get(str(info.get("role", "")))
            if role is None:
                continue
            grouped.setdefault(
                (run_id, role, int(info.get("batch_index", 0)), str(session_id)),
                {
                    "role": info.get("role"),
                    "batch_index": info.get("batch_index", 0),
                    "session_id": session_id,
                    "state": info.get("state", "unknown"),
                    "url": info.get("url"),
                    "acus": info.get("acus"),
                },
            )
    return tuple(
        SessionSummary(
            role=role,  # type: ignore[arg-type]
            batch_index=batch_index,
            state=str(row.get("state", "unknown")),
            acus=float(row["acus"]) if row.get("acus") is not None else None,
            url=str(row["url"]) if row.get("url") else None,
            run_id=run_id,
            replayed=bool(getattr(jobs_by_run.get(run_id), "replayed", False)),
        )
        for (run_id, role, batch_index, _), row in sorted(grouped.items())
    )


def _upload_paths(session: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for upload in session.get("uploads", {}).values():
        root = upload_root(str(upload.rel_path))
        if root not in seen:
            seen.add(root)
            paths.append(root)
    return paths


def build_package_input(
    job: ConversionJob,
    session: dict[str, Any],
    package_name: str,
) -> PackageInput:
    """Freeze a completed job and its safe generated outputs into a package snapshot."""
    jobs = _job_history(job)
    run_rows: dict[str, dict[str, Any]] = {}
    object_records: dict[str, dict[str, Any]] = {}
    session_records: list[dict[str, Any]] = []
    audits_by_run: list[tuple[Any, list[dict[str, Any]]]] = []
    for historical_job in jobs:
        run_row, records = _conversion_rows(session, historical_job.run_id)
        run_rows[str(historical_job.run_id)] = run_row
        object_records.update(
            {row["object_id"]: row for row in records if "object_id" in row and "code_path" in row}
        )
        session_records.extend(row for row in records if "role" in row)
        audits_by_run.append((historical_job, _audit_records(historical_job)))
    run_row = run_rows.get(str(job.run_id), {})
    inventory_rows = _inventory_rows(session)
    inventory_by_id = {str(row["id"]): row for row in inventory_rows}
    ordered_items = [
        item for batch in getattr(job, "all_batches", job.batches) for item in batch.objects
    ]
    upload_by_path = {upload.rel_path: upload for upload in session.get("uploads", {}).values()}
    parsed = _parsed_objects(session, job.source, inventory_rows, upload_by_path)
    parsed_by_id = {
        object_id: _find_parsed(row, parsed) for object_id, row in inventory_by_id.items()
    }

    taken: set[str] = set()
    output_paths: dict[tuple[str, str], str] = {}
    objects: list[PackagedObject] = []
    for item in ordered_items:
        object_id = str(_value(item, "id", ""))
        source_row = inventory_by_id.get(object_id, dict(item) if isinstance(item, dict) else {})
        result = job.results.get(object_id, {})
        record = object_records.get(object_id, {})
        state = str(result.get("status") or record.get("state") or "")
        if state not in _STATUSES:
            state = "cancelled" if job.state == "cancelled" else "failed"

        output_files: list[dict[str, Any]] = []
        if state in {"converted", "expert_review"}:
            path = record.get("code_path")
            if path:
                output_files = json.loads(session["store"].read(path).decode("utf-8"))
            elif result.get("candidate") is not None:
                output_files = list(result["candidate"].files)

        packaged_files = []
        for output in output_files:
            language = str(output.get("language", "sql")).casefold()
            if language not in _LANGUAGES:
                continue
            output_path = str(output.get("rel_path") or source_row.get("file") or "converted.sql")
            source_file = str(source_row.get("file") or output_path)
            extension = (
                Path(output_path).suffix
                or {
                    "sql": ".sql",
                    "python": ".py",
                    "json": ".json",
                    "yaml": ".yaml",
                }[language]
            )
            path_key = (source_file, extension)
            if path_key not in output_paths:
                output_paths[path_key] = converted_path(source_file, extension, taken)
            packaged_files.append(
                PackagedFile(
                    package_path=output_paths[path_key],
                    language=language,  # type: ignore[arg-type]
                    content=secret_mask.mask_secrets(str(output.get("content", ""))),
                    etl_format_id=(
                        str(output["etl_format_id"]) if output.get("etl_format_id") else None
                    ),
                )
            )

        candidate = result.get("candidate")
        checks = list(result.get("checks", []))
        parsed_object = parsed_by_id.get(object_id)
        source_columns = (
            _node_summary(parsed_object.node)["columns"] if parsed_object is not None else []
        )
        source_text = (
            secret_mask.mask_secrets(_source_for(session, source_row, upload_by_path))
            if state in {"converted", "expert_review"}
            else ""
        )
        mappings = (
            _mapping_refs(
                job,
                source_row,
                source_text,
                output_files,
                source_columns,
                checks,
                session.get("kb_path"),
            )
            if state in {"converted", "expert_review"}
            else (
                MappingRef(
                    "object",
                    str(source_row.get("qualified_name", "")),
                    job.name_map.get(
                        str(source_row.get("qualified_name", "")),
                        str(source_row.get("qualified_name", "")),
                    ),
                ),
            )
        )

        dependencies = ()
        try:
            detail = session["inventory"].detail(object_id)
            dependencies = tuple(
                str(edge.get("id") or edge.get("qualified_name") or "")
                for edge in (detail or {}).get("upstream", [])
                if edge.get("id") or edge.get("qualified_name")
            )
        except (KeyError, TypeError):
            pass
        status_checks = _check_summaries(checks)
        confidence = result.get("confidence", record.get("confidence"))
        objects.append(
            PackagedObject(
                object_id=object_id,
                source_qualified_name=str(source_row.get("qualified_name", "")),
                target_qualified_name=job.name_map.get(
                    str(source_row.get("qualified_name", "")),
                    str(source_row.get("qualified_name", "")),
                ),
                object_type=str(source_row.get("type", "object")),
                source_file=str(source_row.get("file", "")),
                source_line_start=int(source_row.get("start_line") or 1),
                status=state,  # type: ignore[arg-type]
                confidence=float(confidence) if confidence is not None else None,
                agreement=(
                    float(result["agreement"]) if result.get("agreement") is not None else None
                ),
                pass_rate=pass_rate(checks) if checks else None,
                escalated=bool(result.get("escalated", False)),
                checks=status_checks,
                files=tuple(packaged_files) if state in {"converted", "expert_review"} else (),
                explanations=tuple(
                    secret_mask.mask_secrets(str(value))
                    for value in (getattr(candidate, "explanations", []) or [])
                )
                if candidate is not None
                else (),
                assumptions=tuple(
                    secret_mask.mask_secrets(str(value))
                    for value in (getattr(candidate, "assumptions", []) or [])
                )
                if candidate is not None
                else (),
                unsupported=tuple(
                    secret_mask.mask_secrets(str(value))
                    for value in (getattr(candidate, "unsupported", []) or [])
                )
                if candidate is not None
                else (),
                dependencies=dependencies,
                mappings=mappings,
                error=(
                    secret_mask.mask_secrets(str(job.errors[object_id]))
                    if object_id in job.errors
                    else None
                ),
            )
        )

    audits = [record for _, records in audits_by_run for record in records]
    consent_records = [
        (historical_job, record)
        for historical_job, records in audits_by_run
        for record in records
        if record.get("action") == "convert.consent"
    ]
    consent_job, consent = next(
        (item for item in consent_records if not getattr(item[0], "replayed", False)),
        consent_records[0] if consent_records else (job, {}),
    )
    consent_details = consent.get("details", {})
    consent_at = _utc_time(consent.get("ts")) or _created_at(
        run_rows.get(str(consent_job.run_id), {}).get("created_at"), None
    )
    session_summaries = _session_summaries(jobs, session_records)
    external = tuple(
        str(row.get("qualified_name", ""))
        for row in inventory_rows
        if row.get("external") in (True, 1, "true")
    )
    created_at = _created_at(run_row.get("created_at"), consent_at)
    counts = getattr(job, "counts", {})
    sent_jobs = [item for item in jobs if not getattr(item, "replayed", False)]
    sent_session_ids = {
        str(row.get("session_id"))
        for row in session_records
        if row.get("session_id")
        and any(str(item.run_id) == str(row.get("run_id")) for item in sent_jobs)
    }
    for historical_job in sent_jobs:
        sent_session_ids.update(getattr(historical_job, "started_sessions", {}))
    objects_sent = sum(
        int(
            record.get("details", {}).get(
                "object_count",
                len(getattr(item, "selected_objects", getattr(item, "objects", ()))),
            )
        )
        for item, records in audits_by_run
        if not getattr(item, "replayed", False)
        for record in records
        if record.get("action") == "convert.consent"
    )
    if not consent_records:
        objects_sent = sum(
            len(getattr(item, "selected_objects", getattr(item, "objects", ())))
            for item in sent_jobs
        )
    egress = EgressSummary(
        consent_at=consent_at,
        objects_sent=objects_sent,
        batches=sum(len(item.batches) for item in sent_jobs),
        sessions=len(sent_session_ids),
        blocked_batches=sum(record.get("action") == "convert.blocked_secret" for record in audits),
        devin_host=str(
            consent_details.get("devin_host")
            or next(
                (getattr(item, "devin_host", "") for item in sent_jobs if item.devin_host),
                getattr(job, "devin_host", ""),
            )
        ),
    )
    resolved_name = normalize_package_name(
        package_name,
        upload_paths=_upload_paths(session),
        source=job.source,
        target=job.target,
    )
    return PackageInput(
        run_id=str(job.run_id),
        package_name=resolved_name,
        source_platform=str(job.source),
        target_platform=str(job.target),
        created_at=created_at,
        confidence_threshold=float(getattr(job, "confidence_threshold", 0.85)),
        objects=tuple(objects),
        external=external,
        sessions=session_summaries,
        egress=egress,
        kb_version=str(session["kb_version"]) if session.get("kb_version") is not None else None,
        notes=tuple(
            f"{key.replace('_', ' ')}: {int(value)}"
            for key, value in sorted(counts.items())
            if isinstance(value, (int, float))
        ),
        base_run_id=getattr(job, "base_run_id", None),
        reconverted_object_ids=tuple(getattr(job, "reconverted_object_ids", ())),
    )


__all__ = ["build_package_input"]
