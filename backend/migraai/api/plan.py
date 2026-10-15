from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from migraai.planning.job import PlanJob
from migraai.planning.local_draft import build_local_draft
from migraai.planning.pdf import render_plan_pdf

router = APIRouter(prefix="/api", tags=["planning"])


class PlanRequest(BaseModel):
    regenerate: bool = False


def _session(request: Request) -> dict:
    session = request.app.state.current_session
    if not session:
        raise HTTPException(status_code=404, detail="No confirmed platform pair.")
    return session


def _record_view(record: dict, current_version: int | None, cached: bool = False) -> dict:
    return {
        "state": "done" if record["origin"] == "devin" else "failed_fallback",
        "progress": {"phase": "done", "elapsed_s": 0, "devin_url": record["devin_url"]},
        "plan": record["payload"],
        "origin": record["origin"],
        "reason": record["reason"],
        "cached": cached,
        "kb_version": record["kb_version"],
        "stale": record["kb_version"] != current_version,
        "label": ("Local draft (Devin unavailable)" if record["origin"] == "local_draft" else None),
    }


def _save_local(request: Request, source: str, target: str, reason: str) -> dict:
    app = request.app
    version = int(app.state.kb_store.current_version or 1)
    plan = build_local_draft(source, target, app.state.kb_store.current_path)
    record = app.state.plan_store.save(
        plan,
        source=source,
        target=target,
        kb_version=version,
        origin="local_draft",
        reason=reason,
    )
    app.state.audit_log.append(
        "system", "plan.fallback", f"{source}:{target}", {"reason": reason, "kb_version": version}
    )
    return _record_view(record, app.state.kb_store.current_version)


def start_plan_flow(request: Request, regenerate: bool = False) -> dict:
    app = request.app
    session = _session(request)
    source, target = session["source"], session["target"]
    version = int(app.state.kb_store.current_version or 1)
    settings = app.state.settings_store.read()
    local_reason = None
    if settings.token_kind == "legacy":
        local_reason = (
            "Legacy Devin API key (apk_) is not supported; create a cog_ personal access token "
            "in Devin Settings > Devin API"
        )
    elif settings.token_kind != "v3" or not settings.devin_org_id:
        local_reason = "Devin token not configured"
    previous_job = app.state.plan_job
    if previous_job and previous_job.is_alive():
        previous_job.cancel()
    if not regenerate:
        cached = app.state.plan_store.latest(source, target, kb_version=version, origin="devin")
        if cached:
            app.state.audit_log.append(
                "user",
                "plan.start",
                f"{source}:{target}",
                {
                    "origin": cached["origin"],
                    "source": source,
                    "target": target,
                    "kb_version": version,
                },
            )
            app.state.audit_log.append(
                "system", "plan.cache_hit", f"{source}:{target}", {"kb_version": version}
            )
            return _record_view(cached, version, cached=True)

    origin = "local_draft" if local_reason else "devin"
    app.state.audit_log.append(
        "user",
        "plan.start",
        f"{source}:{target}",
        {"origin": origin, "source": source, "target": target, "kb_version": version},
    )
    if local_reason:
        return _save_local(request, source, target, local_reason)
    org_id, base_url, pat = app.state.settings_store.runtime_devin_config()
    if not pat.startswith("cog_"):
        return _save_local(request, source, target, "Devin token not configured")
    db_path = app.state.kb_store.current_path
    if db_path is None:
        raise HTTPException(status_code=503, detail="Knowledge base is not available.")
    job = PlanJob(
        source=source,
        target=target,
        db_path=db_path,
        kb_store=app.state.kb_store,
        plan_store=app.state.plan_store,
        audit=app.state.audit_log,
        base_url=base_url,
        org_id=org_id,
        pat=pat,
        acu_cap=settings.plan_acu_cap,
        timeout_minutes=settings.plan_timeout_minutes,
    )
    app.state.plan_job = job
    job.start()
    return job.snapshot()


@router.post("/plan")
def post_plan(payload: PlanRequest, request: Request) -> dict:
    return start_plan_flow(request, regenerate=payload.regenerate)


@router.get("/plan")
def get_plan(request: Request) -> dict:
    session = _session(request)
    current_version = request.app.state.kb_store.current_version
    job = request.app.state.plan_job
    if job and (job.source, job.target) == (session["source"], session["target"]):
        result = job.snapshot()
        record = job.record
        result["stale"] = bool(record and record["kb_version"] != current_version)
        return result
    record = request.app.state.plan_store.latest(session["source"], session["target"])
    if not record:
        return {
            "state": "queued",
            "progress": {"phase": "queued", "elapsed_s": 0, "devin_url": None},
            "plan": None,
            "origin": None,
            "reason": None,
            "cached": False,
            "kb_version": current_version,
            "stale": False,
        }
    return _record_view(record, current_version)


@router.delete("/plan/job")
def delete_plan_job(request: Request) -> dict:
    job = request.app.state.plan_job
    if not job or not job.is_alive():
        raise HTTPException(status_code=409, detail="No planning job is running.")
    job.cancel()
    return job.snapshot()


@router.get("/plan.pdf")
def get_plan_pdf(request: Request) -> Response:
    session = _session(request)
    record = request.app.state.plan_store.latest(session["source"], session["target"])
    if not record:
        raise HTTPException(status_code=404, detail="No migration plan is available.")
    logo = Path(__file__).resolve().parents[1] / "planning" / "assets" / "logo.png"
    if not logo.is_file():
        logo = Path(__file__).resolve().parents[3] / "docs" / "assets" / "logo.png"
    if not logo.is_file():
        raise HTTPException(status_code=500, detail="Migration plan logo is missing.")
    content = render_plan_pdf(record, logo)
    filename = f"MigraAI-plan-{record['source']}-to-{record['target']}.pdf"
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
