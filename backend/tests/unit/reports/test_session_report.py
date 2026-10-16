"""Tests for the session conversion report (Markdown + PDF)."""

from __future__ import annotations

import dataclasses
import logging
import re
from io import BytesIO

import pytest
from pypdf import PdfReader

from migraai.packaging.model import PackageInput
from migraai.packaging.sample import sample_package
from migraai.reports.session_report import (
    _report_html,
    build_report_md,
    build_report_pdf,
)

_HEADINGS = [
    "## 1. Summary",
    "## 2. Source and target",
    "## 3. Scope and inventory",
    "## 4. Data sent to Devin",
    "## 5. Conversion results",
    "## 6. Validation",
    "## 7. Expert review and failed items",
    "## 8. Assumptions, unsupported constructs and masked secrets",
    "## 9. Execution order and next steps",
]


def _evil_package() -> PackageInput:
    pkg = sample_package()
    evil = dataclasses.replace(
        pkg.objects[0],
        source_qualified_name="evil<script>alert(1)</script>|x",
    )
    return dataclasses.replace(pkg, objects=(evil,) + pkg.objects[1:])


def _pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)


def _section(md: str, start: str, end: str) -> str:
    return md[md.index(start) : md.index(end)]


def test_headings_in_order_md_and_pdf() -> None:
    pkg = sample_package()
    md = build_report_md(pkg)
    positions = [md.index(h) for h in _HEADINGS]
    assert positions == sorted(positions)
    text = _pdf_text(build_report_pdf(pkg))
    titles = [h.lstrip("# ") for h in _HEADINGS]
    pdf_positions = [text.index(t) for t in titles]
    assert pdf_positions == sorted(pdf_positions)


def test_summary_counts_and_acus() -> None:
    md = build_report_md(sample_package())
    summary = _section(md, "## 1. Summary", "## 2. Source and target")
    assert "- converted: 2" in summary
    assert "- expert_review: 1" in summary
    assert "- failed: 1" in summary
    assert "- cancelled: 0" in summary
    assert "- Included in package: 3" in summary
    egress = _section(md, "## 4. Data sent to Devin", "## 5. Conversion results")
    assert "- Objects sent: 4" in egress
    assert "- Batches: 1" in egress
    assert "- Sessions: 4" in egress
    assert "- Total ACUs: 4.40" in egress


def test_acu_sum_ignores_none() -> None:
    pkg = sample_package()
    sessions = (dataclasses.replace(pkg.sessions[0], acus=None),) + pkg.sessions[1:]
    pkg = dataclasses.replace(pkg, sessions=sessions)
    md = build_report_md(pkg)
    assert "- Total ACUs: 3.20 (partial)" in md
    assert "Total ACUs: 3.20 (partial)" in _pdf_text(build_report_pdf(pkg))


def test_missing_acus_are_reported_as_unavailable_in_md_and_pdf() -> None:
    pkg = sample_package()
    sessions = tuple(dataclasses.replace(session, acus=None) for session in pkg.sessions)
    pkg = dataclasses.replace(pkg, sessions=sessions)

    assert "- Total ACUs: not reported by the Devin API" in build_report_md(pkg)
    assert "Total ACUs: not reported by the Devin API" in _pdf_text(build_report_pdf(pkg))


def test_kb_version_is_reported_and_missing_version_is_n_a() -> None:
    pkg = dataclasses.replace(sample_package(), kb_version=None)
    assert "- KB version: n/a" in build_report_md(pkg)

    pkg = dataclasses.replace(pkg, kb_version="3")
    assert "- KB version: 3" in build_report_md(pkg)


def test_section_7_failed_and_review_items() -> None:
    md = build_report_md(sample_package())
    section = _section(md, "## 7.", "## 8.")
    assert "load\\_script.sql" in section or "load_script.sql" in section
    assert "All three voter sessions failed." in section
    assert "sales.v\\_active" in section or "sales.v_active" in section
    assert "escalated" in section
    assert "confidence 71% below threshold 85%" in section


def test_evil_name_escaped_md_and_pdf() -> None:
    pkg = _evil_package()
    md = build_report_md(pkg)
    assert "<script" not in md
    assert "&lt;script&gt;" in md
    assert "\\|" in md
    header = next(line for line in md.splitlines() if line.startswith("| Name | Type"))
    row = next(line for line in md.splitlines() if "alert" in line)
    unescaped = re.compile(r"(?<!\\)\|")
    assert len(unescaped.findall(row)) == len(unescaped.findall(header))
    row = next(line for line in md.splitlines() if "alert" in line)
    assert "&lt;/script&gt;" in row
    assert "[path removed]" not in row
    html = _report_html(pkg)
    assert "<script" not in html
    text = _pdf_text(build_report_pdf(pkg)).replace(" ", "").replace("\n", "")
    assert "alert(1)" in text


def test_300_objects_pdf_multipage() -> None:
    pkg = sample_package()
    base = pkg.objects[0]
    objects = tuple(
        dataclasses.replace(
            base,
            object_id=f"obj-{i}",
            source_qualified_name=f"schema.object_{i}",
            target_qualified_name=f"SCHEMA.OBJECT_{i}",
        )
        for i in range(300)
    )
    big = dataclasses.replace(pkg, objects=objects)
    pdf = build_report_pdf(big)
    assert pdf.startswith(b"%PDF")
    assert len(PdfReader(BytesIO(pdf)).pages) > 1


def test_no_pdf_warnings(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        build_report_pdf(sample_package())
        build_report_pdf(_evil_package())
    assert not caplog.records


def test_determinism() -> None:
    pkg = sample_package()
    assert build_report_md(pkg) == build_report_md(pkg)
    assert build_report_pdf(pkg) == build_report_pdf(pkg)


def test_masked_secret_and_no_file_content() -> None:
    pkg = sample_package()
    file = pkg.objects[0].files[0]
    masked_file = dataclasses.replace(file, content=file.content + "\npassword = ***MASKED***\n")
    obj = dataclasses.replace(pkg.objects[0], files=(masked_file,))
    pkg = dataclasses.replace(pkg, objects=(obj,) + pkg.objects[1:])
    md = build_report_md(pkg)
    section = _section(md, "## 8.", "## 9.")
    assert "WARNING" in section
    assert "sales.customer\\_accounts" in section or "sales.customer_accounts" in section
    assert "CREATE TABLE SALES.CUSTOMER_ACCOUNTS" not in md


def test_absolute_paths_scrubbed() -> None:
    pkg = sample_package()
    obj = dataclasses.replace(
        pkg.objects[3],
        error="Failed reading C:\\Users\\bob\\x.sql and /home/bob/y.sql",
    )
    md = build_report_md(dataclasses.replace(pkg, objects=pkg.objects[:3] + (obj,)))
    assert "C:\\Users" not in md
    assert "/home/bob" not in md
    assert "https://devin.example/sessions/a" in md
    assert "n/a" in md
    assert "converted/teradata-testpack/tables-converted.sql" in md


def test_egress_none_and_blocked_batches() -> None:
    pkg = sample_package()
    md = build_report_md(dataclasses.replace(pkg, egress=None))
    assert "No data was sent to Devin." in md
    blocked = dataclasses.replace(pkg.egress, blocked_batches=2)
    md = build_report_md(dataclasses.replace(pkg, egress=blocked))
    assert "WARNING" in md
    assert "blocked" in md


def test_report_html_entities_not_split() -> None:
    pkg = sample_package()
    evil = dataclasses.replace(
        pkg.objects[0],
        source_qualified_name="evil<script>alert(1)</script>|x" + "a" * 170,
    )
    pkg = dataclasses.replace(pkg, objects=(evil,) + pkg.objects[1:])
    body = _report_html(pkg).split("<body>", 1)[1]
    for match in re.finditer(r"&", body):
        assert re.match(r"&[#A-Za-z0-9]+;", body[match.start() :])


def test_report_html_no_needless_breaks() -> None:
    html = _report_html(sample_package())
    assert "sales.customer_accounts" in html
    assert "https://devin.example/sessions/a" in html


def test_report_html_breaks_within_name_budget() -> None:
    pkg = sample_package()
    evil = dataclasses.replace(
        pkg.objects[0],
        source_qualified_name="evil<script>alert(1)</script>|x" + "a" * 170,
    )
    pkg = dataclasses.replace(pkg, objects=(evil,) + pkg.objects[1:])
    html = _report_html(pkg)
    cell = next(
        m.group(1) for m in re.finditer(r"<td>(.*?)</td>", html, re.S) if "evil" in m.group(1)
    )
    name_budget = max(8, int(34 * 0.95) - 2)
    for piece in cell.split(" "):
        assert len(re.findall(r"&[#A-Za-z0-9]+;|.", piece)) <= name_budget


def test_long_name_wraps() -> None:
    pkg = sample_package()
    long_name = "a" * 200
    dotted = ".".join(["segment"] * 30)
    obj = dataclasses.replace(
        pkg.objects[0], source_qualified_name=long_name, target_qualified_name=dotted
    )
    pkg = dataclasses.replace(pkg, objects=(obj,) + pkg.objects[1:])
    html = _report_html(pkg)
    assert long_name not in html  # wrap points inserted for the PDF
    pdf = build_report_pdf(pkg)
    assert pdf.startswith(b"%PDF")
    md = build_report_md(pkg)
    assert long_name in md  # exact name preserved in Markdown
