"""Session conversion report (Markdown and PDF) for a finished run.

Pure functions of ``PackageInput``: no clock, no network, no filesystem other
than the CSS template shipped next to this module.
"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import get_args

import reportlab.rl_config
from xhtml2pdf import pisa

from migraai.packaging.acu import total_acus
from migraai.packaging.model import PackagedObject, PackageInput
from migraai.planning.pdf import render_markdown
from migraai.validation.findings import CheckName

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_ABS_PATH = re.compile(
    r"(?<![\w:/\\])(?:[A-Za-z]:[\\/]|\\\\|/[^\s/'\"<>|]+/[^\s'\"<>|]*)[^\s'\"<>|]*"
)
_ABS_PREFIX = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
_WS = re.compile(r"[\r\n\t]+")
_SAFE_URL = re.compile(r"^https://[^\s()<>\[\]]+$")

_MD_ESCAPES = "\\`*_[]()#+!|~{}"


def _scrub(text: str) -> str:
    """Collapse line whitespace and remove absolute local paths."""
    text = _WS.sub(" ", str(text))
    return _ABS_PATH.sub("[path removed]", text)


def _md(text: object) -> str:
    """Escape a dynamic string for safe use in Markdown text and table cells."""
    text = _scrub(str(text))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for ch in _MD_ESCAPES:
        text = text.replace(ch, "\\" + ch)
    return text


def _source_file(text: str) -> str:
    """Absolute source paths collapse to their basename; the rest is scrubbed."""
    text = str(text)
    if _ABS_PREFIX.match(text):
        text = text.replace("\\", "/").rsplit("/", 1)[-1]
    return _md(text)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else str(round(value * 100))


def _link(url: str | None) -> str:
    if url is None:
        return "n/a"
    if _SAFE_URL.match(url):
        return f"[{_md(url)}]({url})"
    return _md(url)


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _status_counts(pkg: PackageInput) -> dict[str, int]:
    counts = {"converted": 0, "expert_review": 0, "failed": 0, "cancelled": 0}
    for obj in pkg.objects:
        if obj.status in counts:
            counts[obj.status] += 1
    return counts


def _review_reason(obj: PackagedObject, threshold: float) -> str:
    parts = []
    if obj.confidence is None or obj.confidence < threshold:
        conf = "n/a" if obj.confidence is None else f"{round(obj.confidence * 100)}%"
        parts.append(f"confidence {conf} below threshold {round(threshold * 100)}%")
    failed = [c.check for c in obj.checks if c.status == "failed"]
    if failed:
        parts.append("failed checks: " + ", ".join(_md(c) for c in failed))
    if obj.escalated:
        parts.append("escalated")
    return "; ".join(parts) if parts else "flagged for expert review"


def build_report_md(pkg: PackageInput) -> str:
    out: list[str] = ["# MigraAI session conversion report", ""]

    # 1. Summary
    counts = _status_counts(pkg)
    out += ["## 1. Summary", ""]
    out.append(f"- Package: {_md(pkg.package_name)}")
    out.append(f"- Run ID: {_md(pkg.run_id)}")
    out.append(f"- Created: {_md(pkg.created_at)}")
    out.append(f"- Total objects: {len(pkg.objects)}")
    for status in ("converted", "expert_review", "failed", "cancelled"):
        out.append(f"- {status}: {counts[status]}")
    out.append(f"- Included in package: {len(pkg.included())}")
    for note in pkg.notes:
        out.append(f"- {_md(note)}")
    out.append("")

    # 2. Source and target
    out += ["## 2. Source and target", ""]
    out.append(f"- Source platform: {_md(pkg.source_platform)}")
    out.append(f"- Target platform: {_md(pkg.target_platform)}")
    out.append(f"- Confidence threshold: {round(pkg.confidence_threshold * 100)}%")
    out.append(f"- KB version: {_md(pkg.kb_version) if pkg.kb_version else 'n/a'}")
    out.append("")

    # 3. Scope and inventory
    out += ["## 3. Scope and inventory", ""]
    types: dict[str, int] = {}
    for obj in pkg.objects:
        types[obj.object_type] = types.get(obj.object_type, 0) + 1
    out += _table(
        ["Object type", "Count"],
        [[_md(t), str(types[t])] for t in sorted(types)],
    )
    out += ["", "Source files:", ""]
    for sf in sorted({o.source_file for o in pkg.objects}):
        out.append(f"- {_source_file(sf)}")
    out += ["", "External references:", ""]
    if pkg.external:
        for ref in sorted(pkg.external):
            out.append(f"- {_md(ref)}")
    else:
        out.append("None.")
    out.append("")

    # 4. Data sent to Devin
    out += ["## 4. Data sent to Devin", ""]
    if pkg.egress is None:
        out.append("No data was sent to Devin.")
    else:
        eg = pkg.egress
        out.append(f"- Consent given at: {_md(eg.consent_at)}")
        out.append(f"- Devin host: {_md(eg.devin_host)}")
        out.append(f"- Objects sent: {eg.objects_sent}")
        out.append(f"- Batches: {eg.batches}")
        out.append(f"- Sessions: {eg.sessions}")
        out.append(f"- Total ACUs: {total_acus(pkg.sessions)}")
        out.append(f"- Blocked batches: {eg.blocked_batches}")
        if pkg.base_run_id is not None:
            out.append(
                f"Re-converted {len(pkg.reconverted_object_ids)} selected objects in run "
                f"{pkg.run_id}; other results carried over from run {pkg.base_run_id}."
            )
        if eg.blocked_batches > 0:
            out.append("")
            out.append(
                f"**WARNING**: {eg.blocked_batches} batch(es) were blocked because "
                "they contained secrets and were not sent."
            )
        out += ["", ""]
        show_run = pkg.base_run_id is not None or any(item.replayed for item in pkg.sessions)
        out += _table(
            (["Run"] if show_run else []) + ["Role", "Batch", "State", "ACUs", "Link"],
            [
                [
                    *([_md(s.run_id)] if show_run else []),
                    _md(s.role),
                    str(s.batch_index),
                    _md("replayed, nothing sent" if s.replayed else s.state),
                    "n/a" if s.acus is None else f"{s.acus:.2f}",
                    _link(s.url),
                ]
                for s in pkg.sessions
            ],
        )
    out.append("")

    # 5. Conversion results
    out += ["## 5. Conversion results", ""]
    out += _table(
        ["Name", "Type", "Status", "Confidence %", "Agreement %", "Escalated"],
        [
            [
                _md(o.source_qualified_name),
                _md(o.object_type),
                _md(o.status),
                _pct(o.confidence),
                _pct(o.agreement),
                "yes" if o.escalated else "no",
            ]
            for o in pkg.objects
        ],
    )
    out.append("")

    # 6. Validation
    out += ["## 6. Validation", ""]
    included = pkg.included()
    check_order = list(get_args(CheckName))
    tally: dict[str, dict[str, int]] = {}
    findings: dict[str, dict[str, object]] = {}
    for obj in included:
        for check in obj.checks:
            slot = tally.setdefault(check.check, {"passed": 0, "failed": 0, "na": 0})
            if check.status == "passed":
                slot["passed"] += 1
            elif check.status == "failed":
                slot["failed"] += 1
            else:
                slot["na"] += 1
            for finding in check.findings:
                entry = findings.setdefault(
                    finding.rule_id,
                    {
                        "check": finding.check,
                        "severity": finding.severity,
                        "count": 0,
                    },
                )
                entry["count"] = int(entry["count"]) + 1
                if finding.severity == "error":
                    entry["severity"] = "error"
    rows = []
    for name in check_order:
        if name not in tally:
            continue
        slot = tally[name]
        denom = slot["passed"] + slot["failed"]
        rate = f"{round(slot['passed'] / denom * 100)}%" if denom else "n/a"
        rows.append([_md(name), str(slot["passed"]), str(slot["failed"]), str(slot["na"]), rate])
    out += _table(["Check", "Passed", "Failed", "N/A", "Pass rate"], rows)
    out += ["", "Top findings:", ""]
    if findings:
        top = sorted(findings.items(), key=lambda kv: (-int(kv[1]["count"]), kv[0]))[:10]
        out += _table(
            ["Rule", "Check", "Severity", "Count"],
            [
                [
                    _md(rule),
                    _md(str(data["check"])),
                    _md(str(data["severity"])),
                    str(data["count"]),
                ]
                for rule, data in top
            ],
        )
    else:
        out.append("No findings.")
    out.append("")

    # 7. Expert review and failed items
    out += ["## 7. Expert review and failed items", ""]
    rows = []
    for obj in pkg.objects:
        if obj.status == "expert_review":
            reason = _review_reason(obj, pkg.confidence_threshold)
        elif obj.status == "failed":
            reason = _md(obj.error) if obj.error else "no error recorded"
        elif obj.status == "cancelled":
            reason = _md(obj.error) if obj.error else "cancelled before completion"
        else:
            continue
        rows.append([_md(obj.source_qualified_name), _md(obj.status), reason])
    if rows:
        out += _table(["Name", "Status", "Reason"], rows)
    else:
        out.append("None.")
    out.append("")

    # 8. Assumptions, unsupported constructs and masked secrets
    out += ["## 8. Assumptions, unsupported constructs and masked secrets", ""]
    out += ["### Assumptions", ""]
    items = [
        f"- {_md(o.source_qualified_name)}: {_md(a)}" for o in pkg.objects for a in o.assumptions
    ]
    out += items or ["None."]
    out += ["", "### Unsupported constructs", ""]
    items = [
        f"- {_md(o.source_qualified_name)}: {_md(u)}" for o in pkg.objects for u in o.unsupported
    ]
    out += items or ["None."]
    out += ["", "### Masked secrets", ""]
    masked = []
    for o in pkg.objects:
        paths = [f.package_path for f in o.files if "***MASKED***" in f.content]
        if paths:
            joined = ", ".join(_md(p) for p in paths)
            masked.append(
                f"- WARNING: {_md(o.source_qualified_name)} ({joined}) contains the "
                "masked placeholder `***MASKED***`. Replace each one with a secret "
                "reference for the target (for example a secret store or environment "
                "variable) before running."
            )
    out += masked or ["None."]
    out.append("")

    # 9. Execution order and next steps
    out += ["## 9. Execution order and next steps", ""]
    for index, obj in enumerate(pkg.included(), 1):
        paths = ", ".join(f"converted/{_md(f.package_path)}" for f in obj.files)
        out.append(
            f"{index}. {_md(obj.target_qualified_name)} ({_md(obj.object_type)}, "
            f"{_md(obj.status)}) -> {paths}"
        )
    out += ["", "Next steps:", ""]
    out.append(
        "- Review the expert-review items: their converted files carry a `NEEDS-REVIEW` header."
    )
    out.append(
        "- Replace every masked placeholder (`***MASKED***`) with a secret "
        "reference for the target."
    )
    out.append(
        f"- Run the runner in execution/{_md(pkg.target_platform)}/ first in its "
        "default dry-run mode, which prints the ordered plan without connecting; "
        "then rerun with `--execute`."
    )
    out.append("- Add `--include-review` only after the expert-review files are approved.")
    out.append(
        "- MigraAI never connects to the target; the runner uses the target CLI's own config."
    )
    out.append("")
    return "\n".join(out)


_CELL = re.compile(r"(<t[dh][^>]*>)(.*?)(</t[dh]>)", re.S)
_ENTITY = re.compile(r"&[#A-Za-z0-9]+;|.")
_TAG = re.compile(r"(<[^>]+>)")

# Column widths (percent) keyed by the table's header row text.
_COL_WIDTHS = {
    ("Name", "Type", "Status", "Confidence %", "Agreement %", "Escalated"): (
        34,
        12,
        14,
        13,
        13,
        14,
    ),
    ("Role", "Batch", "State", "ACUs", "Link"): (16, 9, 13, 10, 52),
    ("Name", "Status", "Reason"): (35, 15, 50),
}
_TABLE = re.compile(r"<table.*?</table>", re.S)
_ROW = re.compile(r"<tr>.*?</tr>", re.S)
_TH = re.compile(r"<th[^>]*>(.*?)</th>")
_SEP_TOKEN = re.compile(r"(?<=[._/\\-])")


def _elen(text: str) -> int:
    """Length in characters with each HTML entity counted as one."""
    return len(_ENTITY.findall(text))


def _budget(width_pct: float) -> int:
    return max(8, int(width_pct * 0.95) - 2)


def _break_run(run: str, budget: int) -> str:
    """Greedily wrap one unbroken run to ``budget`` chars, entities unsplittable."""
    if _elen(run) <= budget:
        return run
    lines: list[str] = []
    current = ""
    for token in _SEP_TOKEN.split(run):
        if not token:
            continue
        if _elen(token) > budget:
            if current:
                lines.append(current)
                current = ""
            pieces = _ENTITY.findall(token)
            chunks = ["".join(pieces[i : i + budget]) for i in range(0, len(pieces), budget)]
            lines.extend(chunks[:-1])
            current = chunks[-1]
        elif current and _elen(current) + 1 + _elen(token) > budget:
            lines.append(current)
            current = token
        else:
            current = token if not current else current + " " + token
    if current:
        lines.append(current)
    return " ".join(lines)


def _set_col_widths(table: str) -> str:
    headers = tuple(_TH.findall(table))
    widths = _COL_WIDTHS.get(headers)
    if widths is None:
        return table
    index = 0

    def widen(match: re.Match[str]) -> str:
        nonlocal index
        width = widths[index]
        index += 1
        return f'<th width="{width}%">{match.group(1)}</th>'

    return _TH.sub(widen, table)


def _wrap_table(match: re.Match[str]) -> str:
    table = _set_col_widths(match.group(0))
    headers = tuple(_TH.findall(table))
    widths = _COL_WIDTHS.get(headers) or tuple(100.0 / max(1, len(headers)) for _ in headers)
    budgets = [_budget(w) for w in widths]

    def wrap_row(row_match: re.Match[str]) -> str:
        column = 0

        def wrap_cell(cell_match: re.Match[str]) -> str:
            nonlocal column
            open_tag, inner, close_tag = cell_match.groups()
            budget = budgets[min(column, len(budgets) - 1)] if budgets else 100
            column += 1
            segments = _TAG.split(inner)
            for i, segment in enumerate(segments):
                if segment.startswith("<"):
                    continue
                segments[i] = " ".join(_break_run(p, budget) for p in segment.split(" "))
            return open_tag + "".join(segments) + close_tag

        return _CELL.sub(wrap_cell, row_match.group(0))

    return _ROW.sub(wrap_row, table)


def _break_long_words(html: str) -> str:
    return _TABLE.sub(_wrap_table, html)


def _report_html(pkg: PackageInput) -> str:
    css = (_TEMPLATE_DIR / "session_report.css").read_text(encoding="utf-8")
    body = render_markdown(build_report_md(pkg))
    body = body.replace("<table", '<table repeat="1"')
    body = _break_long_words(body)
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        f"{css}</style></head><body>"
        f"{body}</body></html>"
    )


def build_report_pdf(pkg: PackageInput) -> bytes:
    html = _report_html(pkg)
    previous = reportlab.rl_config.invariant
    reportlab.rl_config.invariant = 1
    try:
        output = BytesIO()
        result = pisa.CreatePDF(html, dest=output)
    finally:
        reportlab.rl_config.invariant = previous
    if result.err:
        raise RuntimeError("Could not render session report PDF.")
    return output.getvalue()
