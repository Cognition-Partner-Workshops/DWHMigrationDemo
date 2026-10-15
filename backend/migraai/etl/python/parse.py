"""ETL parser for plain Python files (``.py``).

Sniffs imports: a file importing ``airflow`` delegates to the airflow parser;
one importing ``pyspark`` or referencing ``SparkSession`` delegates to the
spark parser. Everything else is analysed as a plain Python module emitting
``module``/``function`` objects, ``calls`` references, ``etl_reads`` /
``etl_writes`` and ``EmbeddedSql`` entries.
"""

from __future__ import annotations

import ast as pyast
import importlib

from migraai.etl.python.common import (
    EtlBuilder,
    EtlParseResult,
    decode,
    file_unparsed,
)
from migraai.etl.python.module import ModuleAnalyzer
from migraai.etl.python.recover import parse_python_recovering


def _sniff(text: str) -> str:
    """Return 'airflow', 'spark' or 'python' for a .py source."""
    try:
        tree = pyast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return "python"
    airflow = False
    spark = False
    for node in pyast.walk(tree):
        if isinstance(node, pyast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top == "airflow":
                    airflow = True
                elif top in {"pyspark", "delta"}:
                    spark = True
        elif isinstance(node, pyast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top == "airflow":
                airflow = True
            elif top in {"pyspark", "delta"}:
                spark = True
        elif isinstance(node, pyast.Name) and node.id == "SparkSession":
            spark = True
    if airflow:
        return "airflow"
    if spark:
        return "spark"
    return "python"


def parse(data: bytes, rel_path: str) -> EtlParseResult:
    try:
        return _parse(data, rel_path)
    except Exception:
        builder = EtlBuilder("python", rel_path)
        builder.issue("error", "internal", "Unexpected parser failure.")
        result = builder.result()
        result.objects.append(file_unparsed(rel_path, "unexpected parser error"))
        return result


def _parse(data: bytes, rel_path: str) -> EtlParseResult:
    text = decode(data)
    tool = _sniff(text)
    if tool == "airflow":
        module = importlib.import_module("migraai.etl.airflow.parse")
        return module.parse(data, rel_path)
    if tool == "spark":
        module = importlib.import_module("migraai.etl.spark.parse")
        return module.parse(data, rel_path)
    return parse_python_module(data, rel_path, tool="python")


def parse_python_module(
    data: bytes,
    rel_path: str,
    *,
    tool: str,
    call_handler=None,
) -> EtlParseResult:
    """Shared python-module analysis entry point (used by spark for .py)."""
    text = decode(data)
    builder = EtlBuilder(tool, rel_path)
    recovered = parse_python_recovering(text, tool)
    if recovered.tree is None:
        result = builder.result()
        result.objects.append(file_unparsed(rel_path, "unrecoverable syntax errors"))
        result.issues.extend(recovered.issues)
        return result
    analyzer = ModuleAnalyzer(builder, rel_path, call_handler=call_handler)
    analyzer.analyze(recovered.tree, recovered.recovered, recovered.issues)
    return builder.result()


__all__ = ["parse", "parse_python_module"]
