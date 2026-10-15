"""Static checks for Python ETL output formats."""

from __future__ import annotations

import ast
import json

from migraai.validation.findings import Finding


def _finding(format_id: str, name: str, message: str, line: int | None = None) -> Finding:
    return Finding("syntax", f"etl.{format_id}.{name}", "error", message, line)


def _python_tree(format_id: str, content: str) -> tuple[ast.Module | None, list[Finding]]:
    try:
        tree = ast.parse(content)
    except SyntaxError as error:
        return None, [
            _finding(format_id, "parse_error", error.msg, error.lineno),
        ]
    findings = []
    if not tree.body:
        findings.append(_finding(format_id, "empty", "Python file has no statements."))
    return tree, findings


def check_databricks_notebook(format_id: str, content: str) -> list[Finding]:
    tree, findings = _python_tree(format_id, content)
    if tree is None:
        return findings

    lines = content.splitlines()
    if not lines or lines[0].rstrip() != "# Databricks notebook source":
        findings.append(
            _finding(format_id, "missing_header", "Missing Databricks notebook source header.", 1)
        )
    for line_number, line in enumerate(lines, 1):
        if line.strip().startswith("# COMMAND") and line.strip() != "# COMMAND ----------":
            findings.append(
                _finding(
                    format_id,
                    "malformed_cell_separator",
                    "Malformed Databricks cell separator.",
                    line_number,
                )
            )
    return findings


def check_fabric_notebook(format_id: str, content: str) -> list[Finding]:
    tree, findings = _python_tree(format_id, content)
    if tree is None:
        return findings

    lines = content.splitlines()
    if not lines or lines[0].rstrip() != "# Fabric notebook source":
        findings.append(_finding(format_id, "missing_header", "Missing Fabric notebook header.", 1))

    if not any(line.rstrip() == "# CELL ********************" for line in lines):
        findings.append(_finding(format_id, "missing_cell", "Fabric notebook has no cell marker."))

    marker_pattern = "# {} ********************"
    for index, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped.startswith(("# CELL", "# METADATA", "# MARKDOWN")):
            marker_found = any(
                stripped == marker_pattern.format(marker)
                for marker in ("CELL", "METADATA", "MARKDOWN")
            )
            if not marker_found:
                findings.append(
                    _finding(
                        format_id,
                        "malformed_marker",
                        "Malformed Fabric notebook marker.",
                        index + 1,
                    )
                )
        if stripped != "# METADATA ********************":
            continue

        metadata_lines: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        while cursor < len(lines) and (
            lines[cursor].rstrip() == "# META" or lines[cursor].rstrip().startswith("# META ")
        ):
            value = lines[cursor].rstrip()[len("# META") :]
            if value.startswith(" "):
                value = value[1:]
            metadata_lines.append(value)
            cursor += 1
        metadata = "\n".join(metadata_lines).strip()
        try:
            if not metadata:
                raise ValueError("metadata block is empty")
            json.loads(metadata)
        except ValueError:
            findings.append(
                _finding(
                    format_id,
                    "invalid_metadata",
                    "Fabric metadata block must contain valid JSON.",
                    index + 1,
                )
            )
    return findings


def _is_dag_call(function: ast.expr) -> bool:
    return (isinstance(function, ast.Name) and function.id == "DAG") or (
        isinstance(function, ast.Attribute) and function.attr == "DAG"
    )


def _is_dag_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (isinstance(target, ast.Name) and target.id == "dag") or (
        isinstance(target, ast.Attribute) and target.attr == "dag"
    )


def check_airflow_dag(format_id: str, content: str) -> list[Finding]:
    tree, findings = _python_tree(format_id, content)
    if tree is None:
        return findings

    has_airflow_import = False
    dag_calls: list[ast.Call] = []
    decorated_functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            has_airflow_import |= any(alias.name.startswith("airflow") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            has_airflow_import |= bool(node.module and node.module.startswith("airflow"))
        elif isinstance(node, ast.Call) and _is_dag_call(node.func):
            dag_calls.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            _is_dag_decorator(decorator) for decorator in node.decorator_list
        ):
            decorated_functions.append(node)

    if not has_airflow_import:
        findings.append(
            _finding(format_id, "missing_airflow_import", "No Airflow import was found.")
        )
    dag_count = len(dag_calls) + len(decorated_functions)
    if not dag_count:
        findings.append(_finding(format_id, "missing_dag", "No Airflow DAG definition was found."))
    for call in dag_calls:
        if not call.args and not any(keyword.arg == "dag_id" for keyword in call.keywords):
            findings.append(
                _finding(
                    format_id,
                    "missing_dag_id",
                    "DAG call must provide a dag_id.",
                    call.lineno,
                )
            )
    if dag_count > 1:
        lines = [node.lineno for node in [*dag_calls, *decorated_functions]]
        findings.append(
            Finding(
                "syntax",
                f"etl.{format_id}.multiple_dags",
                "warning",
                "More than one DAG definition was found.",
                min(lines) if lines else None,
            )
        )
    return findings
