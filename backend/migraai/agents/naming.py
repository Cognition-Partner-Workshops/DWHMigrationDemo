from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from migraai.platforms.pack import TargetPack


def _get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _quote_identifier(value: str, pack: TargetPack) -> str:
    naming = pack.naming
    if naming.identifier_case == "lower":
        value = value.lower()
    elif naming.identifier_case == "upper":
        value = value.upper()
    requires_quote = re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_$]*", value
    ) is None or value.upper() in set(naming.reserved_words)
    if not requires_quote:
        return value
    quote = naming.quote_char
    return f"{quote}{value.replace(quote, quote + quote)}{quote}"


def build_name_map(objects: Iterable[Any], pack: TargetPack) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in objects:
        if _get(item, "external", False) in (True, 1, "true"):
            continue
        qualified_name = str(_get(item, "qualified_name", "") or "")
        if not qualified_name:
            continue
        schema = _get(item, "schema_name", _get(item, "schema"))
        name = str(_get(item, "name", "") or "")
        parts = [str(schema), name] if schema and name else qualified_name.split(".")[-2:]
        target_name = ".".join(_quote_identifier(part, pack) for part in parts if part)
        result[qualified_name] = target_name
    return result


__all__ = ["build_name_map"]
