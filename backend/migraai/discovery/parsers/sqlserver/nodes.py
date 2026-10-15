from dataclasses import dataclass, field

from migraai.discovery import ast


@dataclass(slots=True)
class TSqlInsert(ast.Insert):
    top: ast.Top | None = None
    output: list[ast.SelectItem] = field(default_factory=list)
    output_into: ast.QualifiedName | None = None


TSqlInsert.kind = "insert"


@dataclass(slots=True)
class TSqlUpdate(ast.Update):
    top: ast.Top | None = None
    output: list[ast.SelectItem] = field(default_factory=list)
    output_into: ast.QualifiedName | None = None


TSqlUpdate.kind = "update"


@dataclass(slots=True)
class TSqlDelete(ast.Delete):
    top: ast.Top | None = None
    output: list[ast.SelectItem] = field(default_factory=list)
    output_into: ast.QualifiedName | None = None


TSqlDelete.kind = "delete"


@dataclass(slots=True)
class TSqlMerge(ast.Merge):
    top: ast.Top | None = None
    output: list[ast.SelectItem] = field(default_factory=list)
    output_into: ast.QualifiedName | None = None


TSqlMerge.kind = "merge"


__all__ = ["TSqlDelete", "TSqlInsert", "TSqlMerge", "TSqlUpdate"]
