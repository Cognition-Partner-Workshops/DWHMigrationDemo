from __future__ import annotations

from dataclasses import dataclass, field

from migraai.discovery import ast
from migraai.discovery.objects import ParsedObject


@dataclass(slots=True)
class EmbeddedSql:
    owner_qualified_name: str
    text: str
    location: str
    dialect_hint: str | None = None


@dataclass(slots=True)
class EtlParseResult:
    objects: list[ParsedObject] = field(default_factory=list)
    embedded_sql: list[EmbeddedSql] = field(default_factory=list)
    issues: list[ast.Issue] = field(default_factory=list)
