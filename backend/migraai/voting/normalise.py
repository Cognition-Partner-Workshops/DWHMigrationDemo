from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from typing import Any

import sqlglot
import yaml

from migraai.platforms.pack import _strip_comments
from migraai.validation.dialects import sqlglot_dialect


def _value(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, Mapping) else getattr(item, name, default)


def _canonical_file(content: str, language: str, target: str) -> str:
    language = language.casefold()
    if language == "sql":
        statements = sqlglot.parse(content, read=sqlglot_dialect(target))
        return "\n".join(
            statement.sql(
                dialect=sqlglot_dialect(target),
                normalize=True,
                pretty=False,
                comments=False,
            )
            for statement in statements
            if statement is not None
        )
    if language == "python":
        return ast.dump(ast.parse(content), annotate_fields=True, include_attributes=False)
    if language == "json":
        value = json.loads(content)
    elif language == "yaml":
        value = yaml.safe_load(content)
    else:
        value = content
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _fallback(content: str, language: str) -> str:
    language = language.casefold()
    stripped = _strip_comments(content, language)
    if language == "yaml":
        stripped = re.sub(r"(?m)#.*$", "", stripped)
    return " ".join(re.findall(r"[\w$]+|[^\w\s]", stripped.casefold(), flags=re.UNICODE))


def normalise(content: str, language: str, target: str) -> str:
    try:
        return _canonical_file(content, language, target)
    except Exception:
        return _fallback(content, language)


def normalise_files(files: list[Any], target: str) -> str:
    ordered = sorted(files, key=lambda item: str(_value(item, "rel_path", "")))
    normalized = []
    for item in ordered:
        path = str(_value(item, "rel_path", ""))
        content = str(_value(item, "content", ""))
        language = str(_value(item, "language", "sql"))
        normalized.append(f"{path}\n{normalise(content, language, target)}")
    return "\n".join(normalized)


normalize = normalise
normalize_files = normalise_files

__all__ = ["normalise", "normalise_files", "normalize", "normalize_files"]
