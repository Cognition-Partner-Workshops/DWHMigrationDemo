from __future__ import annotations

import importlib
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from migraai.packaging.parts import collect_parts
from migraai.packaging.snapshot import build_package_input
from migraai.packaging.zipper import build_zip

router = APIRouter(prefix="/api", tags=["packaging"])
_REPORT_MODULE = "migraai.reports.session_report"
_REPORT_UNAVAILABLE = "Report generator not available yet."


def _completed_package(
    request: Request,
    name: str | None,
    *,
    require_included: bool,
):
    session = request.app.state.current_session
    if not session or not session.get("source") or not session.get("target"):
        raise HTTPException(status_code=409, detail="Confirm a source and target first.")
    job = request.app.state.convert_job
    if job is None:
        raise HTTPException(status_code=409, detail="No conversion job exists.")
    if job.state not in {"done", "cancelled"}:
        raise HTTPException(
            status_code=409,
            detail=f"Conversion is {job.state}; wait until it is done or cancelled.",
        )
    package = build_package_input(job, session, name or "")
    if require_included and not package.included():
        raise HTTPException(
            status_code=409,
            detail="The conversion has no converted or expert-review objects to download.",
        )
    return job, session, package


def _report_module():
    try:
        return importlib.import_module(_REPORT_MODULE)
    except ModuleNotFoundError as error:
        if error.name == _REPORT_MODULE:
            raise HTTPException(status_code=503, detail=_REPORT_UNAVAILABLE) from error
        raise


@router.get("/download")
def get_download(
    request: Request,
    name: str | None = Query(default=None),
) -> StreamingResponse:
    job, _, package = _completed_package(request, name, require_included=True)
    payload = build_zip(package, collect_parts(package))
    counts: dict[str, int] = {}
    for item in package.objects:
        counts[item.status] = counts.get(item.status, 0) + 1
    request.app.state.audit_log.append(
        "user",
        "download.package",
        job.run_id,
        {
            "included": len(package.included()),
            "excluded": len(package.excluded()),
            "converted": counts.get("converted", 0),
            "expert_review": counts.get("expert_review", 0),
        },
    )
    filename = f"{package.package_name}-converted.zip"
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _report(request: Request, report_type: str) -> Response:
    module = _report_module()
    _, _, package = _completed_package(request, None, require_included=False)
    if report_type == "md":
        content = module.build_report_md(package)
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="session-report.md"'},
        )
    content = module.build_report_pdf(package)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="session-report.pdf"'},
    )


@router.get("/report.md")
def get_report_md(request: Request) -> Response:
    return _report(request, "md")


@router.get("/report.pdf")
def get_report_pdf(request: Request) -> Response:
    return _report(request, "pdf")
