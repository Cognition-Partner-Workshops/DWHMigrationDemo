from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from pypdf import PdfReader

from migraai.core.secret_mask import contains_secret, mask_secrets

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
GUIDES = (
    ("install-guide.md", "Installation-Guide.pdf"),
    ("user-guide.md", "User-Guide.pdf"),
    ("operations-guide.md", "Operations-Guide.pdf"),
)
REPORT_TEMPLATES_README = DOCS_DIR / "report-templates" / "README.md"
WINDOWS_USER_PATH = re.compile(r"(?i)C:[\\/]+Users[\\/]+([^\\/\\s]+)")


def _load_builder() -> ModuleType:
    path = ROOT / "packaging" / "docs" / "build_docs_pdf.py"
    spec = importlib.util.spec_from_file_location("docs_pdf_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load PDF builder at {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _heading_texts(markdown: str) -> list[str]:
    parser = MarkdownIt("js-default", {"html": False}).enable("table")
    headings: list[str] = []
    tokens = parser.parse(markdown)
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.tag not in {"h1", "h2"}:
            continue
        if index + 1 >= len(tokens) or tokens[index + 1].type != "inline":
            continue
        inline = tokens[index + 1]
        value = "".join(
            child.content
            for child in inline.children or []
            if child.type in {"text", "code_inline", "html_inline", "image"}
        )
        headings.append(value.strip())
    return headings


def _normalise_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _relative_markdown_links(path: Path) -> list[str]:
    parser = MarkdownIt("js-default", {"html": False})
    links = []
    for token in parser.parse(path.read_text(encoding="utf-8")):
        for child in token.children or []:
            if child.type == "link_open":
                href = child.attrGet("href")
                if href:
                    links.append(href)
    return links


def _is_generic_windows_path_placeholder(value: str) -> bool:
    return (value.startswith("%") and value.endswith("%")) or (
        value.startswith("<") and value.endswith(">")
    )


def test_guide_pdfs_have_pages_and_preserve_source_headings(tmp_path: Path) -> None:
    builder = _load_builder()
    built_paths = builder.build_docs_pdfs(tmp_path)
    documents = list(GUIDES)
    if REPORT_TEMPLATES_README.is_file():
        documents.append(("report-templates/README.md", "Report-Templates.pdf"))

    assert {path.name for path in built_paths} == {pdf_name for _, pdf_name in documents}
    for source_name, pdf_name in documents:
        source = (DOCS_DIR / source_name).read_text(encoding="utf-8")
        pdf_path = tmp_path / pdf_name
        assert pdf_path.is_file()
        reader = PdfReader(pdf_path)
        assert len(reader.pages) >= 1
        pdf_text = _normalise_whitespace(
            " ".join(page.extract_text() or "" for page in reader.pages)
        )
        for heading in _heading_texts(source):
            assert _normalise_whitespace(heading) in pdf_text


def test_pdf_markdown_sources_contain_no_local_paths_or_secrets() -> None:
    sources = [DOCS_DIR / source_name for source_name, _ in GUIDES]
    if REPORT_TEMPLATES_README.is_file():
        sources.append(REPORT_TEMPLATES_README)

    for path in sources:
        source = path.read_text(encoding="utf-8")
        assert not any(marker in source for marker in ("/home/", "/Users/", "/tmp/"))
        for match in WINDOWS_USER_PATH.finditer(source):
            assert _is_generic_windows_path_placeholder(match.group(1))
        assert mask_secrets(source) == source
        assert not contains_secret(source)


def test_relative_markdown_links_in_docs_resolve() -> None:
    sources = sorted(DOCS_DIR.glob("*.md"))
    if REPORT_TEMPLATES_README.is_file():
        sources.append(REPORT_TEMPLATES_README)

    for source in sources:
        for href in _relative_markdown_links(source):
            parts = urlsplit(href)
            if parts.scheme or parts.netloc or not parts.path.casefold().endswith(".md"):
                continue
            target = (source.parent / unquote(parts.path)).resolve()
            assert target.is_file(), f"{source.relative_to(ROOT)} links to missing file {href!r}"
