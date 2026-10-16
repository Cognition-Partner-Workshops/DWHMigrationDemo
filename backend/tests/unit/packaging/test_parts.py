from __future__ import annotations

from types import SimpleNamespace

from migraai.packaging import parts
from migraai.packaging.sample import sample_package


def test_collect_parts_skips_missing_child_modules(monkeypatch) -> None:
    def missing_module(name: str):
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(parts.importlib, "import_module", missing_module)

    assert parts.collect_parts(sample_package()) == {}


def test_collect_parts_prefixes_generator_outputs(monkeypatch) -> None:
    modules = {
        "migraai.packaging.workbook": SimpleNamespace(
            build_workbook=lambda _: b"xlsx",
            workbook_csvs=lambda _: {"objects.csv": b"csv"},
        ),
        "migraai.packaging.scripts": SimpleNamespace(
            build_execution_scripts=lambda _: {"snowflake/run.sql": b"script"}
        ),
        "migraai.reports.session_report": SimpleNamespace(
            build_report_md=lambda _: "# Report",
            build_report_pdf=lambda _: b"pdf",
        ),
    }
    monkeypatch.setattr(parts, "_optional_module", modules.get)

    assert parts.collect_parts(sample_package()) == {
        "mapping/mapping-workbook.xlsx": b"xlsx",
        "mapping/csv/objects.csv": b"csv",
        "execution/snowflake/run.sql": b"script",
        "report/session-report.md": b"# Report",
        "report/session-report.pdf": b"pdf",
    }
