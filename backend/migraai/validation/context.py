from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from migraai.platforms.pack import TargetPack


@dataclass(slots=True)
class OutputFile:
    rel_path: str
    language: str
    content: str
    etl_format_id: str | None = None


@dataclass(slots=True)
class ValidationContext:
    target: str
    pack: TargetPack
    name_map: dict[str, str]
    converted: set[str]
    external: set[str]
    order: list[str]
    nameable_dependencies: set[str]
    table_columns: dict[str, list[tuple[str, str]]]
    type_map: dict[str, list[str]]
    source_grants: dict[str, set[tuple[str, str]]]


@dataclass(slots=True)
class ObjectUnderTest:
    source_qualified_name: str
    object_type: str
    dependencies: list[str]
    source_column_types: list[tuple[str, str]]
    files: list[OutputFile]
