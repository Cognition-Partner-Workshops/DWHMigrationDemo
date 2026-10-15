from __future__ import annotations

import importlib
from collections.abc import Callable
from types import ModuleType
from typing import Any

SQL_PARSERS: dict[str, str] = {
    dialect: f"migraai.discovery.parsers.{dialect}"
    for dialect in (
        "teradata",
        "oracle",
        "sqlserver",
        "snowflake",
        "synapse",
        "db2",
        "hive",
        "netezza",
        "ansi",
    )
}
ETL_PARSERS: dict[str, str] = {
    tool: f"migraai.etl.{tool}.parse"
    for tool in (
        "informatica",
        "datastage",
        "ssis",
        "airflow",
        "controlm",
        "shell",
        "python",
        "spark",
    )
}


def _import(module_path: str) -> ModuleType | None:
    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError as error:
        if error.name and (module_path == error.name or module_path.startswith(f"{error.name}.")):
            return None
        raise


def get_sql_parser(dialect: str) -> type[Any] | None:
    module_path = SQL_PARSERS.get(dialect.casefold())
    if module_path is None:
        return None
    module = _import(module_path)
    return getattr(module, "Parser", None) if module is not None else None


def get_etl_parser(tool: str) -> Callable[..., Any] | None:
    module_path = ETL_PARSERS.get(tool.casefold())
    if module_path is None:
        return None
    module = _import(module_path)
    return getattr(module, "parse", None) if module is not None else None


__all__ = ["ETL_PARSERS", "SQL_PARSERS", "get_etl_parser", "get_sql_parser"]
