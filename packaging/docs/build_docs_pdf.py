from __future__ import annotations

import argparse
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

import reportlab.rl_config
from bs4 import BeautifulSoup
from migraai.planning.pdf import render_markdown
from xhtml2pdf import pisa

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
DOCUMENTS = (
    ("install-guide.md", "Installation-Guide.pdf"),
    ("user-guide.md", "User-Guide.pdf"),
    ("operations-guide.md", "Operations-Guide.pdf"),
)
REPORT_TEMPLATES_README = DOCS_DIR / "report-templates" / "README.md"
REPORT_TEMPLATES_PDF = "Report-Templates.pdf"
PDF_BY_SOURCE = {
    (DOCS_DIR / source_name).resolve(): pdf_name for source_name, pdf_name in DOCUMENTS
}
PDF_BY_SOURCE[REPORT_TEMPLATES_README.resolve()] = REPORT_TEMPLATES_PDF


def _render_document(source_path: Path) -> bytes:
    markdown = source_path.read_text(encoding="utf-8")
    body = BeautifulSoup(render_markdown(markdown), "html.parser")
    for link in body.find_all("a", href=True):
        href = str(link["href"])
        parts = urlsplit(href)
        if parts.scheme or parts.netloc or not parts.path.casefold().endswith(".md"):
            continue
        target = (source_path.parent / unquote(parts.path)).resolve()
        pdf_name = PDF_BY_SOURCE.get(target) if target.is_file() else None
        if pdf_name:
            link["href"] = pdf_name
        else:
            link.unwrap()

    logo_uri = escape((DOCS_DIR / "assets" / "logo.png").resolve().as_uri(), quote=True)
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 18mm; }}
body {{ font-family: sans-serif; color: #222; font-size: 10pt; }}
h1, h2, h3 {{ color: #17365d; }}
h1 {{ border-bottom: 1px solid #b8c5d6; padding-bottom: 4px; }}
img {{ margin-bottom: 12px; }}
table {{ width: 100%; border-collapse: collapse; }}
td, th {{ border: 1px solid #777; padding: 4px; }}
pre {{ white-space: pre-wrap; background-color: #f3f5f7; padding: 6px; }}
code {{ font-family: monospace; }}
a {{ color: #145b9b; }}
</style></head><body>
<img src="{logo_uri}" width="150">
{body}
</body></html>"""

    previous = reportlab.rl_config.invariant
    reportlab.rl_config.invariant = 1
    try:
        output = BytesIO()
        result = pisa.CreatePDF(html, dest=output)
    finally:
        reportlab.rl_config.invariant = previous
    if result.err:
        raise RuntimeError(
            f"Could not render documentation PDF from {source_path.name}."
        )
    return output.getvalue()


def build_docs_pdfs(out_dir: Path) -> list[Path]:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = list(DOCUMENTS)
    if REPORT_TEMPLATES_README.is_file():
        documents.append(("report-templates/README.md", REPORT_TEMPLATES_PDF))
    else:
        print(
            "Skipping Report-Templates.pdf: "
            "docs/report-templates/README.md does not exist yet."
        )

    output_paths = []
    for source_name, pdf_name in documents:
        source_path = DOCS_DIR / source_name
        output_path = output_dir / pdf_name
        output_path.write_bytes(_render_document(source_path))
        output_paths.append(output_path)
    return output_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build PDF copies of the MigraAI guides."
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="Directory for generated PDFs."
    )
    args = parser.parse_args()
    for path in build_docs_pdfs(args.out):
        print(path)


if __name__ == "__main__":
    main()
