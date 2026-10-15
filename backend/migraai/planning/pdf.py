from __future__ import annotations

from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from xhtml2pdf import pisa

from migraai.planning.schema import Plan

_PDF_TRANSLATION = str.maketrans(
    {
        "→": "->",
        "←": "<-",
        "≤": "<=",
        "≥": ">=",
        "≠": "!=",
        "✓": "[x]",
        "✗": "[ ]",
        "×": "x",
    }
)


def _pdf_text(value: str) -> str:
    translated = value.translate(_PDF_TRANSLATION)
    return translated.encode("cp1252", errors="replace").decode("cp1252")


def render_markdown(value: str) -> str:
    value = _pdf_text(value)
    try:
        renderer = MarkdownIt("js-default", {"html": False})
    except ValueError:
        renderer = MarkdownIt("default", {"html": False})
    return renderer.enable("table").render(value)


def render_plan_pdf(record: dict[str, Any], logo_path: Path) -> bytes:
    plan = Plan.model_validate(record["payload"])
    sections = []
    toc = []
    for section in plan.sections:
        toc.append(
            f'<li><a href="#{section.id}">{section.id}. {escape(_pdf_text(section.title))}</a></li>'
        )
        content = render_markdown(section.content)
        footnotes = []
        for index, citation in enumerate(section.citations, 1):
            reference = (
                f"KB-{citation.kb_id}"
                if citation.kb_id is not None
                else escape(_pdf_text(citation.url or ""))
            )
            footnotes.append(
                f'<li id="{section.id}-fn-{index}">{reference} — '
                f"{escape(_pdf_text(citation.note))}</li>"
            )
        citation_html = f"<ol>{''.join(footnotes)}</ol>" if footnotes else ""
        sections.append(
            f'<section><h2 id="{section.id}">{section.id}. '
            f"{escape(_pdf_text(section.title))}</h2>"
            f"{content}{citation_html}</section>"
        )
    session_id = record.get("devin_session_id")
    details = (
        f"<p>Devin session: {escape(_pdf_text(session_id))}</p>"
        if session_id
        else f"<p>{escape(_pdf_text(record.get('reason') or ''))}</p>"
    )
    logo = logo_path.resolve().as_uri()
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
    @page {{ size: A4; margin: 18mm; }} body {{ font-family: sans-serif; }}
    h1,h2 {{ color: #17365d; }} section {{ page-break-before: always; }}
    table {{ width: 100%; }}
    td,th {{ border: 1px solid #777; padding: 4px; }}
    .cover {{ page-break-after: always; }} .small {{ color: #555; }}
    </style></head><body>
    <div class="cover"><img src="{logo}" width="160"><h1>MigraAI Migration Plan</h1>
    <h2>{escape(_pdf_text(plan.source))} → {escape(_pdf_text(plan.target))}</h2>
    <p>Date: {escape(_pdf_text(str(record.get("created_at", ""))))}</p>
    <p>KB version: {escape(_pdf_text(str(record.get("kb_version", ""))))}</p>
    <p>Origin: {escape(_pdf_text(record.get("origin", "")))}</p>{details}</div>
    <h1>Table of Contents</h1><ol>{"".join(toc)}</ol>{"".join(sections)}
    </body></html>"""
    html = _pdf_text(html)
    output = BytesIO()
    result = pisa.CreatePDF(html, dest=output)
    if result.err:
        raise RuntimeError("Could not render migration plan PDF.")
    return output.getvalue()
