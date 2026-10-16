from __future__ import annotations

import importlib
from types import ModuleType

from migraai.packaging.model import PackageInput


def _optional_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name == name:
            return None
        raise


def collect_parts(package: PackageInput) -> dict[str, bytes]:
    parts: dict[str, bytes] = {}
    workbook = _optional_module("migraai.packaging.workbook")
    if workbook is not None:
        parts["mapping/mapping-workbook.xlsx"] = workbook.build_workbook(package)
        for name, content in workbook.workbook_csvs(package).items():
            parts[f"mapping/csv/{name}"] = content

    scripts = _optional_module("migraai.packaging.scripts")
    if scripts is not None:
        for path, content in scripts.build_execution_scripts(package).items():
            parts[f"execution/{path}"] = content

    report = _optional_module("migraai.reports.session_report")
    if report is not None:
        parts["report/session-report.md"] = report.build_report_md(package).encode("utf-8")
        parts["report/session-report.pdf"] = report.build_report_pdf(package)
    return parts


__all__ = ["collect_parts"]
