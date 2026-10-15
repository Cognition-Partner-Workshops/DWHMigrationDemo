from __future__ import annotations

import ast as py_ast
import io
import json
import re
from dataclasses import dataclass, field

from defusedxml import ElementTree

_XML_ROOTS = {
    "POWERMART": "informatica",
    "REPOSITORY": "informatica",
    "DSEXPORT": "datastage",
    "DTS:EXECUTABLE": "ssis",
    "DEFTABLE": "controlm",
}


@dataclass(slots=True)
class Detection:
    detected_type: str
    parser_type: str | None
    dialect: str | None = None
    text: str | None = None
    issues: list[str] = field(default_factory=list)


def decode_source(data: bytes) -> tuple[str, str, list[str]]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig", []
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16", []
    try:
        return data.decode("utf-8"), "utf-8", []
    except UnicodeDecodeError:
        return data.decode("cp1252"), "cp1252", ["Source encoding fell back to cp1252."]


def _xml_root(data: bytes) -> str | None:
    try:
        iterator = ElementTree.iterparse(io.BytesIO(data), events=("start",))
        _, root = next(iterator)
    except Exception:
        return None
    return str(root.tag).rsplit("}", 1)[-1].upper()


def _python_kinds(text: str) -> tuple[bool, bool]:
    try:
        tree = py_ast.parse(text)
    except (SyntaxError, ValueError):
        lowered = text.casefold()
        return (
            bool(re.search(r"(?:from|import)\s+airflow\b", lowered))
            and ("dag(" in lowered or "@dag" in lowered),
            bool(re.search(r"(?:from|import)\s+pyspark\b", lowered)),
        )
    airflow = False
    spark = False
    has_dag = False
    for node in py_ast.walk(tree):
        if isinstance(node, (py_ast.Import, py_ast.ImportFrom)):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, py_ast.Import)
                else [node.module or ""]
            )
            airflow |= any(name == "airflow" or name.startswith("airflow.") for name in names)
            spark |= any(name == "pyspark" or name.startswith("pyspark.") for name in names)
        if isinstance(node, (py_ast.Call, py_ast.FunctionDef, py_ast.AsyncFunctionDef)):
            name = (
                node.func.id
                if isinstance(node, py_ast.Call) and isinstance(node.func, py_ast.Name)
                else ""
            )
            has_dag |= name == "DAG" or any(
                isinstance(decorator, py_ast.Name) and decorator.id == "dag"
                for decorator in getattr(node, "decorator_list", [])
            )
    return airflow and has_dag, spark


def detect_file(rel_path: str, data: bytes, source_dialect: str) -> Detection:
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = "." + name.rsplit(".", 1)[-1].casefold() if "." in name else ""
    text, _, issues = decode_source(data)
    dialect = source_dialect.casefold()

    if suffix in {".sql", ".ddl", ".txt", ".bteq"}:
        if dialect not in {"sqlserver", "synapse"} and re.search(r"(?im)^\s*GO\s*(?:--.*)?$", text):
            issues.append("SQL contains GO batch separators, which may indicate another dialect.")
        return Detection(f"sql:{dialect}", "sql", dialect, text, issues)
    if suffix == ".dtsx":
        return Detection("ssis", "ssis", text=text, issues=issues)
    if suffix == ".dsx":
        return Detection("datastage", "datastage", text=text, issues=issues)

    root = _xml_root(data) if suffix == ".xml" or data.lstrip().startswith(b"<") else None
    xml_kind = _XML_ROOTS.get(root or "")
    if root == "EXECUTABLE":
        xml_kind = "ssis"
    if xml_kind:
        kind = xml_kind
        return Detection(kind, kind, text=text, issues=issues)
    if suffix == ".json":
        try:
            document = json.loads(text)
            keys = (
                {str(key).casefold() for key in document} if isinstance(document, dict) else set()
            )
            if keys & {"definitions", "jobs", "folders", "run_now", "runnow"}:
                return Detection("controlm", "controlm", text=text, issues=issues)
        except (json.JSONDecodeError, RecursionError):
            pass
    if suffix == ".py":
        airflow, spark = _python_kinds(text)
        if airflow:
            return Detection("airflow", "airflow", text=text, issues=issues)
        if spark:
            return Detection("spark", "spark", text=text, issues=issues)
        return Detection("python", "python", text=text, issues=issues)
    if suffix == ".scala" and "sparksession" in text.casefold():
        return Detection("spark", "spark", text=text, issues=issues)
    if suffix == ".sh":
        return Detection("shell", "shell", text=text, issues=issues)
    return Detection("unsupported", None, text=text, issues=issues)
