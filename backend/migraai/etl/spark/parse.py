"""ETL parser for Spark sources.

- ``.py`` files containing pyspark: reuses the Python module analysis with
  extra handlers for ``spark.sql`` / ``spark.table`` / read & write chains /
  ``DeltaTable.forName`` / JDBC.
- ``.scala`` files: token-based extraction (objects ``scala_object`` +
  ``function``). The core never routes ``.scala`` here — a core change request.
- ``.json`` files: Control-M Automation API shapes delegate lazily to
  ``migraai.etl.controlm.parse`` (warning ``spark.controlm_unavailable`` when
  that module is absent); anything else is treated as a Spark/Livy submit
  config producing one ``spark_submit`` object.
"""

from __future__ import annotations

import ast as pyast
import importlib
import json
from pathlib import PurePosixPath
from typing import Any

from migraai.discovery import ast
from migraai.etl.python.common import (
    EtlBuilder,
    EtlParseResult,
    decode,
    file_unparsed,
    json_node,
    normalize_path,
    qname,
    span,
)
from migraai.etl.python.module import ModuleAnalyzer, Scope
from migraai.etl.python.pyconvert import resolve_string
from migraai.etl.python.recover import parse_python_recovering
from migraai.etl.spark.scala import parse_scala

# ---------------------------------------------------------------------------
# pyspark call handling
# ---------------------------------------------------------------------------

_PATH_WRITE_METHODS = {"save", "saveAsTextFile"}
_PATH_METHODS = {
    "parquet",
    "csv",
    "json",
    "orc",
    "text",
    "avro",
    "load",
    "sequenceFile",
    "objectFile",
}
_TABLE_READS = {"table"}
_TABLE_WRITES = {"saveAsTable", "insertInto", "writeTo"}


def _chain(call: pyast.Call) -> list[tuple[str, pyast.Call | None]]:
    """Flatten ``a.b(1).c(2).d()`` → [('d', outer call), ('c', …), ('b', …), ('a', None)]."""
    parts: list[tuple[str, pyast.Call | None]] = []
    node: pyast.AST = call
    while isinstance(node, pyast.Call):
        func = node.func
        if isinstance(func, pyast.Attribute):
            parts.append((func.attr, node))
            node = func.value
        elif isinstance(func, pyast.Name):
            parts.append((func.id, node))
            node = None
            break
        else:
            node = func
    while isinstance(node, pyast.Attribute):
        parts.append((node.attr, None))
        node = node.value
    if isinstance(node, pyast.Name):
        parts.append((node.id, None))
    return parts


def _chain_names(call: pyast.Call) -> set[str]:
    return {name for name, _ in _chain(call)}


def _option_args(call: pyast.Call, scope: Scope) -> tuple[str | None, str | None]:
    key = resolve_string(call.args[0], scope.constants) if call.args else None
    value = resolve_string(call.args[1], scope.constants) if len(call.args) > 1 else None
    return key, value


def _jdbc_format(parts: list[tuple[str, pyast.Call | None]], scope: Scope) -> bool:
    for name, call in parts:
        if name == "format" and call is not None and call.args:
            value = resolve_string(call.args[0], scope.constants)
            return (value or "").casefold() == "jdbc"
    return False


def spark_call(call: pyast.Call, scope: Scope, dotted: str, builder: EtlBuilder) -> bool:
    """Handle a pyspark-flavoured call; return True if ETL-relevant."""
    sp = span(call.lineno, call.end_lineno or call.lineno)
    names = _chain_names(call)
    if dotted:
        last = dotted.split(".")[-1]
    elif isinstance(call.func, pyast.Attribute):
        last = call.func.attr
    else:
        last = ""
    parts = _chain(call)

    if last == "sql" and ({"spark", "sqlContext", "sparkSession"} & names or "sql" in names):
        sql = resolve_string(call.args[0], scope.constants) if call.args else None
        if sql is None and call.args:
            scope.issues.append(
                ast.Issue(
                    "info",
                    "spark.dynamic_sql",
                    "Could not resolve spark.sql(...) argument.",
                    sp,
                )
            )
        elif sql is not None:
            builder.embed(scope.owner, sql, "spark.sql")
        return True
    if last == "forName" or "DeltaTable.forName" in dotted:
        target = call.args[1] if len(call.args) > 1 else (call.args[0] if call.args else None)
        name = resolve_string(target, scope.constants) if target is not None else None
        if name:
            scope.hints.append(builder.ref(name, "etl_reads", sp))
        return True
    if last in _TABLE_READS and call.args:
        name = resolve_string(call.args[0], scope.constants)
        if name:
            scope.hints.append(builder.ref(name, "etl_reads", sp))
        return True
    if last in _TABLE_WRITES and call.args:
        name = resolve_string(call.args[0], scope.constants)
        if name:
            scope.hints.append(builder.ref(name, "etl_writes", sp))
        return True
    if last == "jdbc" and len(call.args) >= 2:
        name = resolve_string(call.args[1], scope.constants)
        if name:
            write = "write" in names
            scope.hints.append(builder.ref(name, "etl_writes" if write else "etl_reads", sp))
        return True
    if last == "load" or "load" in names:
        write = "write" in names
        if _jdbc_format(parts, scope):
            for part_name, part_call in parts:
                if part_name == "option" and part_call is not None:
                    key, value = _option_args(part_call, scope)
                    if key == "dbtable" and value:
                        scope.hints.append(
                            builder.ref(value, "etl_writes" if write else "etl_reads", sp)
                        )
                    elif key == "query" and value:
                        builder.embed(scope.owner, value, "jdbc.query")
            return True
        name = resolve_string(call.args[0], scope.constants) if call.args else None
        if name:
            scope.hints.append(
                builder.ref(name, "etl_writes" if write else "etl_reads", sp, split=False)
            )
        return True
    if last in _PATH_METHODS | _PATH_WRITE_METHODS:
        if last == "format":
            return False
        name = resolve_string(call.args[0], scope.constants) if call.args else None
        write = "write" in names or last in _PATH_WRITE_METHODS
        if name:
            scope.hints.append(
                builder.ref(name, "etl_writes" if write else "etl_reads", sp, split=False)
            )
        return True
    return False


# ---------------------------------------------------------------------------
# .json submit configs + Control-M sniff
# ---------------------------------------------------------------------------

_CONTROLM_TYPES = ("folder", "simplefolder", "calendar", "job", "flow")


def _looks_like_controlm(payload: Any) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    typed = [
        value
        for value in payload.values()
        if isinstance(value, dict) and isinstance(value.get("Type"), str)
    ]
    if not typed:
        return False
    return all(value["Type"].casefold().startswith(_CONTROLM_TYPES) for value in typed)


def _parse_json(text: str, rel_path: str, builder: EtlBuilder) -> None:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError) as error:
        builder.issue("error", "syntax", f"Malformed JSON: {error}")
        builder.objects.append(file_unparsed(rel_path, "malformed JSON"))
        return
    if _looks_like_controlm(payload):
        try:
            module = importlib.import_module("migraai.etl.controlm.parse")
        except ImportError:
            builder.issue(
                "warning",
                "controlm_unavailable",
                "Control-M JSON detected but the controlm parser is not available.",
            )
            return
        delegated = module.parse(text.encode("utf-8"), rel_path)
        builder.objects.extend(getattr(delegated, "objects", []))
        builder.embedded_sql.extend(getattr(delegated, "embedded_sql", []))
        builder.issues.extend(getattr(delegated, "issues", []))
        return
    _SUBMIT_KEYS = {
        "file",
        "application_file",
        "primary_file",
        "className",
        "main_class",
        "mainClass",
        "spark_python_task",
        "spark_jar_task",
        "name",
        "proxyUser",
        "driverMemory",
        "executorMemory",
        "numExecutors",
        "jars",
        "pyFiles",
        "conf",
        "args",
    }
    if not isinstance(payload, dict) or not (_SUBMIT_KEYS & payload.keys()):
        builder.issue("warning", "unrecognized_json", "JSON is not a Spark submit config.")
        return
    _spark_submit_object(payload, rel_path, builder, text)


def _key_line(text: str, key: str) -> int:
    needle = f'"{key}"'
    for index, line in enumerate(text.split("\n"), 1):
        if needle in line:
            return index
    return 1


def _spark_submit_object(
    payload: dict[str, Any], rel_path: str, builder: EtlBuilder, text: str
) -> None:
    refs: list[ast.RefHint] = []
    args: dict[str, Any] = {}
    file_value = (
        payload.get("file") or payload.get("application_file") or payload.get("primary_file")
    )
    class_name = payload.get("className") or payload.get("main_class") or payload.get("mainClass")
    if isinstance(file_value, str) and file_value:
        normalized = normalize_path(file_value, rel_path)
        file_line = _key_line(text, "file")
        args["file"] = ast.Literal(type="string", value=normalized, span=span(file_line))
        refs.append(builder.ref(normalized, "etl_runs", span(file_line), split=False))
    if isinstance(class_name, str) and class_name:
        class_line = _key_line(text, "className")
        args["className"] = qname(class_name)
        refs.append(builder.ref(class_name, "etl_runs", span(class_line)))
    for key in ("args", "jars", "pyFiles", "files", "archives"):
        value = payload.get(key)
        if isinstance(value, list):
            args[key] = json_node(value)
    conf = payload.get("conf") or payload.get("spark_conf")
    if isinstance(conf, dict):
        args["conf"] = json_node(conf)
    node = ast.DialectStatement(name="spark.submit", args=args, references=refs, span=span(1, 1))
    builder.add("spark_submit", rel_path.replace("\\", "/"), node, 1, 1)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def parse(data: bytes, rel_path: str) -> EtlParseResult:
    try:
        return _parse(data, rel_path)
    except Exception:
        builder = EtlBuilder("spark", rel_path)
        builder.issue("error", "internal", "Unexpected parser failure.")
        result = builder.result()
        result.objects.append(file_unparsed(rel_path, "unexpected parser error"))
        return result


def _parse(data: bytes, rel_path: str) -> EtlParseResult:
    suffix = PurePosixPath(rel_path.replace("\\", "/")).suffix.casefold()
    if suffix == ".scala":
        builder = EtlBuilder("spark", rel_path)
        parse_scala(decode(data), builder)
        return builder.result()
    if suffix == ".json":
        builder = EtlBuilder("spark", rel_path)
        _parse_json(decode(data), rel_path, builder)
        return builder.result()
    # .py delegated from python.parse (pyspark/SparkSession detected)
    text = decode(data)
    builder = EtlBuilder("spark", rel_path)
    recovered = parse_python_recovering(text, "spark")
    if recovered.tree is None:
        result = builder.result()
        result.objects.append(file_unparsed(rel_path, "unrecoverable syntax errors"))
        result.issues.extend(recovered.issues)
        return result
    analyzer = ModuleAnalyzer(builder, rel_path, call_handler=spark_call)
    analyzer.analyze(recovered.tree, recovered.recovered, recovered.issues)
    return builder.result()


__all__ = ["parse"]
