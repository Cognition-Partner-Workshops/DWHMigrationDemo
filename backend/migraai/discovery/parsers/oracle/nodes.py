from dataclasses import dataclass, field

from migraai.discovery import ast


@dataclass(slots=True)
class OracleSelect(ast.Select):
    hierarchical: ast.DialectExpr | None = None
    into_list: list[ast.Node] = field(default_factory=list)
    bulk_collect: bool = False


OracleSelect.kind = "select"


@dataclass(slots=True)
class OracleFetch(ast.Fetch):
    bulk_collect: bool = False
    limit: ast.Node | None = None


OracleFetch.kind = "fetch"


__all__ = ["OracleFetch", "OracleSelect"]
