from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import ModuleType

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "docs" / "report-templates" / "samples"
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


def _load_generator() -> ModuleType:
    path = ROOT / "docs" / "report-templates" / "generate_samples.py"
    spec = importlib.util.spec_from_file_location("report_sample_generator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load report sample generator from {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generated_samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    generator = _load_generator()
    output_dir = tmp_path_factory.mktemp("report-samples") / "samples"
    return generator.generate(output_dir)


def _pdf_text(path: Path) -> str:
    reader = PdfReader(io.BytesIO(path.read_bytes()))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_report_samples_match_generator(generated_samples: dict[str, Path]) -> None:
    for name in SAMPLE_FILES:
        committed = SAMPLE_DIR / name
        generated = generated_samples.get(name)
        failure_message = f"Report sample drifted: {name}. Regenerate with: {REGENERATION_COMMAND}"
        if generated is None or not committed.is_file():
            pytest.fail(failure_message)
        if name == "migration-plan.pdf":
            # Render-generated resource names differ between equivalent plan PDF renders.
            assert _pdf_text(generated) == _pdf_text(committed), failure_message
            continue
        assert generated.read_bytes() == committed.read_bytes(), failure_message
