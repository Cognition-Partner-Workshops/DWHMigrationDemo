from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from migraai.agents.batching import plan_batches
from migraai.agents.job import ConversionJob
from migraai.devin.client import build_conversion_client_factory
from migraai.validation.findings import CheckResult
from migraai.validation.validate import pass_rate

router = APIRouter(prefix="/api", tags=["conversion"])
CHECK_NAMES = (
    "syntax",
    "lint",
    "semantic",
    "rule",
    "dependency",
    "object_reference",
    "security",
)
NO_DEVIN_MESSAGE = (
    "Devin isn't configured. Add a Devin API key in Settings to convert; nothing was sent."
)


def _session(request: Request) -> dict[str, Any]:
    session = request.app.state.current_session
    if not session or not session.get("source") or not session.get("target"):
        raise HTTPException(status_code=409, detail="Confirm a source and target first.")
    return session


def _inventory(session: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory = session["inventory"]
    objects: list[dict[str, Any]] = []
    page = 1
    while True:
        current = inventory.page(page=page, page_size=300)["items"]
        objects.extend(current)
        if len(current) < 300:
            break
        page += 1
    with inventory.lock:
        edges = [
            dict(row)
            for row in inventory.connection.execute(
                "SELECT source_id, target_id, kind, external FROM dependency"
            )
        ]
    return objects, edges


def devin_configuration(request: Request) -> tuple[bool, str]:
    settings = request.app.state.settings_store.read()
    mock_mode = os.environ.get("MIGRAAI_TEST_MOCK_DEVIN") == "1"
    replay_mode = bool(os.environ.get("MIGRAAI_TEST_REPLAY_DEVIN"))
    configured = settings.token_kind in {"legacy", "v3"} and (
        settings.token_kind != "v3" or bool(settings.devin_org_id)
    )
    parsed = urlsplit(settings.devin_api_base_url)
    return (
        mock_mode or replay_mode or configured,
        "local replay"
        if replay_mode
        else "local mock"
        if mock_mode
        else (parsed.netloc or "Devin"),
    )


def _validated_object_ids(objects: list[dict[str, Any]], object_ids: Any) -> list[str] | None:
    if object_ids is None:
        return None
    if (
        not isinstance(object_ids, list)
        or not object_ids
        or any(not isinstance(object_id, str) or not object_id for object_id in object_ids)
        or len(set(object_ids)) != len(object_ids)
    ):
        raise HTTPException(
            status_code=400,
            detail="object_ids must be a non-empty list of unique inventory IDs.",
        )
    known_ids = {str(item.get("id", "")) for item in objects}
    if any(object_id not in known_ids for object_id in object_ids):
        raise HTTPException(status_code=400, detail="object_ids contains an unknown inventory ID.")
    return object_ids


def _estimate(
    request: Request,
    session: dict[str, Any],
    object_ids: list[str] | None = None,
) -> dict[str, Any]:
    settings = request.app.state.settings_store.read()
    objects, edges = _inventory(session)
    selected_ids = _validated_object_ids(objects, object_ids)
    selected = set(selected_ids) if selected_ids is not None else None
    local_objects = [
        item
        for item in objects
        if item.get("external") not in (True, 1, "true")
        and (selected is None or str(item.get("id", "")) in selected)
    ]
    if not local_objects:
        raise HTTPException(status_code=409, detail="No selected parsed objects to convert.")
    batches = plan_batches(
        objects if selected_ids is None else local_objects,
        edges,
        settings.conversion_batch_size,
    )
    configured, host = devin_configuration(request)
    voter_sessions = len(batches) * 3
    max_escalation_sessions = len(batches)
    return {
        "objects": len(local_objects),
        "batches": len(batches),
        "oversized_batches": sum(batch.oversized for batch in batches),
        "voter_sessions": voter_sessions,
        "max_escalation_sessions": max_escalation_sessions,
        "max_sessions": voter_sessions + max_escalation_sessions,
        "batch_size": settings.conversion_batch_size,
        "session_timeout_minutes": settings.conversion_session_timeout_minutes,
        "max_concurrent_sessions": settings.max_concurrent_sessions,
        "devin_configured": configured,
        "devin_host": host,
        "acu_cap": None,
    }


@router.post("/convert/estimate")
def post_convert_estimate(
    request: Request,
    object_ids: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    session = _session(request)
    return _estimate(request, session, object_ids)


@router.post("/convert", response_model=None)
def post_convert(
    request: Request, payload: dict[str, Any] | None = None
) -> dict[str, str] | JSONResponse:
    if not payload or payload.get("consent") is not True:
        raise HTTPException(status_code=400, detail="Consent is required before conversion.")
    session = _session(request)
    objects, _ = _inventory(session)
    object_ids = _validated_object_ids(objects, payload.get("object_ids"))
    estimate = _estimate(request, session, object_ids)
    with request.app.state.convert_lock:
        current_job = request.app.state.convert_job
        if current_job is not None and current_job.is_alive():
            raise HTTPException(status_code=409, detail="A conversion is already active.")
        base = None
        if object_ids is not None:
            same_session = current_job is not None and current_job.session.get(
                "session_id"
            ) == session.get("session_id")
            if not same_session or current_job.state not in {"done", "cancelled"}:
                raise HTTPException(
                    status_code=409,
                    detail="Convert selected objects needs a finished conversion to merge with.",
                )
            base = current_job
        if not estimate["devin_configured"]:
            return JSONResponse(
                status_code=412,
                content={"code": "devin_not_configured", "message": NO_DEVIN_MESSAGE},
            )
        org_id, base_url, pat = request.app.state.settings_store.runtime_devin_config()
        settings = request.app.state.settings_store.read()
        _, devin_host = devin_configuration(request)
        client_factory, replayed = build_conversion_client_factory(base_url, org_id, pat)
        job = ConversionJob(
            session=session,
            audit=request.app.state.audit_log,
            devin_pat=pat,
            org_id=org_id,
            base_url=base_url,
            batch_size=settings.conversion_batch_size,
            timeout_minutes=settings.conversion_session_timeout_minutes,
            max_concurrent_sessions=settings.max_concurrent_sessions,
            confidence_threshold=settings.confidence_threshold,
            devin_host=devin_host,
            client_factory=client_factory,
            consent=True,
            object_ids=object_ids,
            base=base,
            replayed=replayed,
        )
        request.app.state.convert_job = job
        job.start()
    return {"job_id": job.run_id}


def _job(request: Request, job_id: str) -> ConversionJob:
    job = request.app.state.convert_job
    if job is None or job.run_id != job_id:
        raise HTTPException(status_code=404, detail="Unknown conversion job.")
    return job


def _batch_counts(job: ConversionJob, object_ids: list[str], batch_state: str) -> dict[str, int]:
    counts = {
        "converted": 0,
        "escalated": 0,
        "expert_review": 0,
        "failed": 0,
        "cancelled": 0,
        "pending": 0,
    }
    for object_id in object_ids:
        result = job.results.get(object_id)
        if result is not None:
            counts[result["status"]] += 1
            counts["escalated"] += int(bool(result.get("escalated")))
        elif batch_state == "failed":
            counts["failed"] += 1
        elif batch_state == "cancelled":
            counts["cancelled"] += 1
        else:
            counts["pending"] += 1
    return counts


@router.get("/convert/jobs/{job_id}")
def get_convert_job(job_id: str, request: Request) -> dict[str, Any]:
    job = _job(request, job_id)
    snapshot = job.snapshot()
    batches = []
    for index, batch in snapshot["progress"]["batches"].items():
        batches.append(
            {
                "index": int(index),
                **batch,
                "object_count": len(batch["objects"]),
                "counts": _batch_counts(job, batch["objects"], batch["status"]),
            }
        )
    snapshot["batches"] = batches
    return snapshot


@router.get("/convert/jobs/{job_id}/events")
def get_convert_job_events(job_id: str, request: Request) -> StreamingResponse:
    job = _job(request, job_id)

    def events() -> Iterator[str]:
        yield from job.event_stream()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/convert/jobs/{job_id}/cancel")
def cancel_convert_job(job_id: str, request: Request) -> dict[str, Any]:
    job = _job(request, job_id)
    if not job.is_alive():
        raise HTTPException(status_code=409, detail="The conversion is no longer active.")
    job.cancel()
    return get_convert_job(job_id, request)


def _check_dict(check: CheckResult) -> dict[str, Any]:
    return {
        "check": check.check,
        "status": check.status,
        "findings": [
            {
                "check": finding.check,
                "rule_id": finding.rule_id,
                "severity": finding.severity,
                "message": finding.message,
                "line": finding.line,
            }
            for finding in check.findings
        ],
    }


def _objects_for_results(job: ConversionJob) -> list[dict[str, Any]]:
    session_objects = {str(item.get("id", "")): item for item in job.objects}
    batch_by_object = {
        object_id: job.batch_status[index]["status"]
        for index, batch in enumerate(job.batches)
        for object_id in batch.object_ids
    }
    rows: list[dict[str, Any]] = []
    for object_id, source in session_objects.items():
        result = job.results.get(object_id)
        if result is None:
            batch_state = batch_by_object.get(object_id, "queued")
            status = (
                "failed"
                if batch_state == "failed"
                else (
                    "cancelled"
                    if batch_state == "cancelled" or job.state == "cancelled"
                    else "pending"
                )
            )
            confidence = agreement_value = None
            checks: dict[str, str] = {}
            escalated = False
            rate = None
        else:
            status = result["status"]
            confidence = result.get("confidence")
            agreement_value = result.get("agreement")
            checks = {check.check: check.status for check in result.get("checks", [])}
            escalated = bool(result.get("escalated"))
            rate = pass_rate(result.get("checks", []))
        rows.append(
            {
                "object_id": object_id,
                "qualified_name": source.get("qualified_name", ""),
                "type": source.get("type", ""),
                "status": status,
                "confidence": confidence,
                "agreement": agreement_value,
                "pass_rate": rate,
                "escalated": escalated,
                "checks": checks,
                "error": job.errors.get(object_id),
            }
        )
    return rows


@router.get("/conversions")
def get_conversions(
    request: Request,
    status: str | None = Query(default=None),
    object_type: str | None = Query(default=None, alias="type"),
    search: str = Query(default=""),
    failed_check: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=300),
) -> dict[str, Any]:
    job = request.app.state.convert_job
    rows = _objects_for_results(job) if job is not None else []
    if status:
        rows = [item for item in rows if item["status"] == status]
    if object_type:
        rows = [item for item in rows if item["type"] == object_type]
    if search:
        needle = search.casefold()
        rows = [item for item in rows if needle in item["qualified_name"].casefold()]
    if failed_check:
        rows = [item for item in rows if item["checks"].get(failed_check) == "failed"]
    total = len(rows)
    offset = (page - 1) * page_size
    return {
        "items": rows[offset : offset + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def _read_code(session: dict[str, Any], path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    return json.loads(session["store"].read(path).decode("utf-8"))


@router.get("/conversions/{object_id}")
def get_conversion_detail(object_id: str, request: Request) -> dict[str, Any]:
    session = _session(request)
    job = request.app.state.convert_job
    if job is None or not any(str(item.get("id", "")) == object_id for item in job.objects):
        raise HTTPException(status_code=404, detail="Unknown converted object.")
    inventory = session["inventory"]
    object_row = None
    candidate_rows = []
    session_rows = []
    historical_job = job
    while historical_job is not None and object_row is None:
        with inventory.lock:
            object_row = inventory.connection.execute(
                "SELECT state,confidence,picked_voter,code_path FROM conversion_object "
                "WHERE run_id=? AND object_id=?",
                (historical_job.run_id, object_id),
            ).fetchone()
            if object_row is not None:
                candidate_rows = inventory.connection.execute(
                    "SELECT voter,state,self_confidence,code_path FROM conversion_candidate "
                    "WHERE run_id=? AND object_id=?",
                    (historical_job.run_id, object_id),
                ).fetchall()
                session_rows = inventory.connection.execute(
                    "SELECT role,url,state,acus,error FROM conversion_session "
                    "WHERE run_id=? AND object_id=? ORDER BY rowid",
                    (historical_job.run_id, object_id),
                ).fetchall()
        historical_job = getattr(historical_job, "base", None)
    result = job.results.get(object_id)
    final_candidate = result.get("candidate") if result else None
    candidates: dict[str, Any] = {voter: None for voter in ("A", "B", "C")}
    sessions_by_role = {row["role"]: dict(row) for row in session_rows}
    for row in candidate_rows:
        voter = row["voter"]
        session_record = sessions_by_role.get(voter, {})
        error = session_record.get("error")
        if error is None and row["state"] != "done" and session_record.get("state") == "done":
            error = "No conversion output was returned for this object."
        candidates[voter] = {
            "files": _read_code(session, row["code_path"]),
            "self_confidence": row["self_confidence"],
            "state": session_record.get("state", row["state"]),
            "error": error,
        }
    final = {
        "files": _read_code(session, object_row["code_path"] if object_row else None),
        "explanations": list(getattr(final_candidate, "explanations", []) or []),
        "assumptions": list(getattr(final_candidate, "assumptions", []) or []),
        "unsupported": list(getattr(final_candidate, "unsupported", []) or []),
    }
    checks = [_check_dict(check) for check in (result.get("checks", []) if result else [])]
    escalation = next(
        (item for item in (dict(row) for row in session_rows) if item["role"] == "escalation"),
        None,
    )
    return {
        "final": final,
        "error": job.errors.get(object_id),
        "picked_voter": object_row["picked_voter"] if object_row else None,
        "candidates": candidates,
        "pairwise": result.get("pairwise", {}) if result else {},
        "checks": checks,
        "escalation": escalation,
        "devin_sessions": [
            {
                "role": row["role"],
                "url": row["url"],
                "state": row["state"],
                "acus": row["acus"],
            }
            for row in session_rows
        ],
    }


__all__ = ["router"]
