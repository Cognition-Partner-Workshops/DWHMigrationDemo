from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, ClassVar


def _snake_case(name: str) -> str:
    name = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def _is_omitted(value: Any) -> bool:
    return value is None or (isinstance(value, (dict, list, tuple, set, frozenset)) and not value)


def _serialize(value: Any) -> Any:
    if isinstance(value, Node):
        return value.to_dict()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items() if not _is_omitted(item)}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value if not _is_omitted(item)]
    if is_dataclass(value):
        return {
            item.name: _serialize(getattr(value, item.name))
            for item in fields(value)
            if not _is_omitted(getattr(value, item.name))
        }
    return value


@dataclass(slots=True, frozen=True)
class Span:
    start_line: int = 1
    start_col: int = 1
    end_line: int = 1
    end_col: int = 1

    def to_dict(self) -> dict[str, int]:
        return {
            "start_line": self.start_line,
            "start_col": self.start_col,
            "end_line": self.end_line,
            "end_col": self.end_col,
        }


@dataclass(slots=True)
class Node:
    span: Span = field(default_factory=Span, kw_only=True)
    kind: ClassVar[str] = "node"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        cls.kind = _snake_case(cls.__name__)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind, "span": self.span.to_dict()}
        for item in fields(self):
            if item.name == "span":
                continue
            value = getattr(self, item.name)
            if _is_omitted(value):
                continue
            result[item.name] = _serialize(value)
        return result

    def walk(self) -> Iterator[Node]:
        return walk(self)


def _child_nodes(value: Any) -> Iterator[Node]:
    if isinstance(value, Node):
        yield from walk(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _child_nodes(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _child_nodes(item)


def walk(node: Node) -> Iterator[Node]:
    yield node
    for item in fields(node):
        if item.name != "span":
            yield from _child_nodes(getattr(node, item.name))


@dataclass(slots=True)
class Identifier(Node):
    value: str = ""
    quoted: bool = False


@dataclass(slots=True)
class QualifiedName(Node):
    parts: list[Identifier] = field(default_factory=list)


@dataclass(slots=True)
class Literal(Node):
    type: str = "string"
    value: str = ""


@dataclass(slots=True)
class ColumnRef(Node):
    name: QualifiedName = field(default_factory=QualifiedName)


@dataclass(slots=True)
class Alias(Node):
    name: Identifier = field(default_factory=Identifier)
    expr: Expr | None = None


@dataclass(slots=True)
class Star(Node):
    qualifier: QualifiedName | None = None
    except_: list[Identifier] = field(default_factory=list)
    replace: list[Alias] = field(default_factory=list)


@dataclass(slots=True)
class Param(Node):
    text: str = ""


@dataclass(slots=True)
class Variable(Node):
    name: str = ""


@dataclass(slots=True)
class Unary(Node):
    op: str = ""
    operand: Expr | None = None


@dataclass(slots=True)
class Binary(Node):
    op: str = ""
    left: Expr | None = None
    right: Expr | None = None


@dataclass(slots=True)
class Between(Node):
    expr: Expr | None = None
    low: Expr | None = None
    high: Expr | None = None
    negated: bool = False


@dataclass(slots=True)
class InList(Node):
    expr: Expr | None = None
    items: list[Expr] = field(default_factory=list)
    negated: bool = False


@dataclass(slots=True)
class InSubquery(Node):
    expr: Expr | None = None
    query: Query | None = None
    negated: bool = False


@dataclass(slots=True)
class Like(Node):
    expr: Expr | None = None
    pattern: Expr | None = None
    escape: Expr | None = None
    op: str = "like"
    negated: bool = False
    quantifier: str | None = None


@dataclass(slots=True)
class IsNull(Node):
    expr: Expr | None = None
    negated: bool = False


@dataclass(slots=True)
class Exists(Node):
    query: Query | None = None
    negated: bool = False


@dataclass(slots=True)
class Quantified(Node):
    op: str = ""
    quantifier: str = ""
    query: Query | None = None


@dataclass(slots=True)
class When(Node):
    cond: Expr | None = None
    result: Expr | None = None


@dataclass(slots=True)
class Case(Node):
    operand: Expr | None = None
    whens: list[When] = field(default_factory=list)
    else_: Expr | None = None


@dataclass(slots=True)
class DataType(Node):
    name: QualifiedName | str = ""
    params: list[Expr | str] = field(default_factory=list)
    children: list[DataType | StructField] = field(default_factory=list)
    array_dims: list[int | None] = field(default_factory=list)


@dataclass(slots=True)
class Cast(Node):
    expr: Expr | None = None
    to: DataType | None = None
    style: str = "cast"
    format: Expr | None = None


@dataclass(slots=True)
class NamedArg(Node):
    name: Identifier = field(default_factory=Identifier)
    value: Expr | None = None


@dataclass(slots=True)
class FunctionCall(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    args: list[Expr | NamedArg] = field(default_factory=list)
    distinct: bool = False
    star: bool = False
    filter: Expr | None = None
    within_group: list[OrderItem] = field(default_factory=list)
    over: WindowSpec | None = None
    ignore_nulls: bool | None = None


@dataclass(slots=True)
class WindowSpec(Node):
    name: Identifier | None = None
    partition_by: list[Expr] = field(default_factory=list)
    order_by: list[OrderItem] = field(default_factory=list)
    frame: Frame | None = None


@dataclass(slots=True)
class FrameBound(Node):
    bound_type: str = "current_row"
    value: Expr | None = None


@dataclass(slots=True)
class Frame(Node):
    unit: str = "rows"
    start: FrameBound | Expr | str | None = None
    end: FrameBound | Expr | str | None = None
    exclude: str | None = None


@dataclass(slots=True)
class Interval(Node):
    value: Expr | None = None
    unit: str = ""
    to_unit: str | None = None


@dataclass(slots=True)
class Extract(Node):
    field: str = ""
    expr: Expr | None = None


@dataclass(slots=True)
class Subquery(Node):
    query: Query | None = None


@dataclass(slots=True)
class ArrayLiteral(Node):
    items: list[Expr] = field(default_factory=list)


@dataclass(slots=True)
class StructField(Node):
    name: Identifier = field(default_factory=Identifier)
    type: DataType | None = None
    value: Expr | None = None


@dataclass(slots=True)
class StructLiteral(Node):
    fields: list[StructField] = field(default_factory=list)


@dataclass(slots=True)
class Subscript(Node):
    expr: Expr | None = None
    index: Expr | None = None


@dataclass(slots=True)
class FieldAccess(Node):
    expr: Expr | None = None
    field: Identifier = field(default_factory=Identifier)


@dataclass(slots=True)
class Collate(Node):
    expr: Expr | None = None
    collation: QualifiedName = field(default_factory=QualifiedName)


@dataclass(slots=True)
class Lambda(Node):
    params: list[Identifier] = field(default_factory=list)
    body: Expr | None = None


@dataclass(slots=True)
class DialectExpr(Node):
    name: str = ""
    args: dict[str, Node | list[Node] | str | None] = field(default_factory=dict)


@dataclass(slots=True)
class OrderItem(Node):
    expr: Expr | None = None
    desc: bool = False
    nulls: str | None = None


@dataclass(slots=True)
class SelectItem(Node):
    expr: Expr | None = None
    alias: Identifier | None = None


@dataclass(slots=True)
class TableRef(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    alias: Identifier | None = None
    column_aliases: list[Identifier] = field(default_factory=list)
    temporal: DialectExpr | None = None
    sample: Expr | None = None
    hints: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SubqueryRef(Node):
    query: Query | None = None
    alias: Identifier | None = None
    lateral: bool = False


@dataclass(slots=True)
class TableFunctionRef(Node):
    call: FunctionCall | None = None
    alias: Identifier | None = None
    lateral: bool = False


@dataclass(slots=True)
class Join(Node):
    join_type: str = "inner"
    left: FromItem | None = None
    right: FromItem | None = None
    on: Expr | None = None
    using: list[Identifier] = field(default_factory=list)


@dataclass(slots=True)
class ValuesRow(Node):
    values: list[Expr] = field(default_factory=list)


@dataclass(slots=True)
class Values(Node):
    rows: list[ValuesRow] = field(default_factory=list)


@dataclass(slots=True)
class ValuesRef(Node):
    values: Values | None = None
    alias: Identifier | None = None
    column_aliases: list[Identifier] = field(default_factory=list)


@dataclass(slots=True)
class SetOperation(Node):
    op: str = "union"
    all: bool = False
    left: Query | None = None
    right: Query | None = None


@dataclass(slots=True)
class Limit(Node):
    count: Expr | None = None
    offset: Expr | None = None
    fetch_first: bool = False
    with_ties: bool = False
    percent: bool = False


@dataclass(slots=True)
class Top(Node):
    count: Expr | None = None
    percent: bool = False
    with_ties: bool = False


@dataclass(slots=True)
class Rollup(Node):
    items: list[Expr] = field(default_factory=list)


@dataclass(slots=True)
class Cube(Node):
    items: list[Expr] = field(default_factory=list)


@dataclass(slots=True)
class GroupingSets(Node):
    sets: list[list[Expr]] = field(default_factory=list)


@dataclass(slots=True)
class GroupBy(Node):
    items: list[Node] = field(default_factory=list)
    rollup: list[Rollup] = field(default_factory=list)
    cube: list[Cube] = field(default_factory=list)
    grouping_sets: list[GroupingSets] = field(default_factory=list)
    all: bool = False


@dataclass(slots=True)
class Cte(Node):
    name: Identifier = field(default_factory=Identifier)
    columns: list[Identifier] = field(default_factory=list)
    query: Query | None = None
    materialized: bool | None = None


@dataclass(slots=True)
class With(Node):
    recursive: bool = False
    ctes: list[Cte] = field(default_factory=list)


@dataclass(slots=True)
class Select(Node):
    distinct: bool = False
    distinct_on: list[Expr] = field(default_factory=list)
    top: Top | None = None
    projections: list[SelectItem] = field(default_factory=list)
    into: QualifiedName | None = None
    from_: list[FromItem] = field(default_factory=list)
    where: Expr | None = None
    group_by: GroupBy | None = None
    having: Expr | None = None
    qualify: Expr | None = None
    windows: dict[str, WindowSpec] = field(default_factory=dict)
    sample: Expr | None = None
    hints: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Query(Node):
    with_: With | None = None
    body: Select | SetOperation | Values | Subquery | None = None
    order_by: list[OrderItem] = field(default_factory=list)
    limit: Limit | None = None


@dataclass(slots=True)
class DefaultValues(Node):
    pass


@dataclass(slots=True)
class Assignment(Node):
    target: Expr | None = None
    value: Expr | None = None


@dataclass(slots=True)
class Insert(Node):
    target: QualifiedName = field(default_factory=QualifiedName)
    columns: list[Identifier] = field(default_factory=list)
    source: Query | Values | DefaultValues | None = None
    overwrite: bool = False
    partition: list[Assignment] = field(default_factory=list)
    conflict: Node | None = None


@dataclass(slots=True)
class Update(Node):
    target: QualifiedName = field(default_factory=QualifiedName)
    alias: Identifier | None = None
    assignments: list[Assignment] = field(default_factory=list)
    from_: list[FromItem] = field(default_factory=list)
    where: Expr | None = None


@dataclass(slots=True)
class Delete(Node):
    target: QualifiedName = field(default_factory=QualifiedName)
    alias: Identifier | None = None
    using: list[FromItem] = field(default_factory=list)
    where: Expr | None = None


@dataclass(slots=True)
class MergeClause(Node):
    matched: bool = False
    condition: Expr | None = None
    action: Node | None = None


@dataclass(slots=True)
class Merge(Node):
    target: QualifiedName = field(default_factory=QualifiedName)
    alias: Identifier | None = None
    source: FromItem | None = None
    on: Expr | None = None
    clauses: list[MergeClause] = field(default_factory=list)


@dataclass(slots=True)
class Truncate(Node):
    target: QualifiedName = field(default_factory=QualifiedName)


@dataclass(slots=True)
class RefHint(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    ref_kind: str = "reads"


@dataclass(slots=True)
class DialectStatement(Node):
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    references: list[RefHint] = field(default_factory=list)


@dataclass(slots=True)
class Copy(Node):
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Load(Node):
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ColumnConstraint(Node):
    constraint_kind: str = ""
    name: Identifier | None = None
    columns: list[Identifier] = field(default_factory=list)
    ref_table: QualifiedName | None = None
    ref_columns: list[Identifier] = field(default_factory=list)
    expr: Expr | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ColumnDef(Node):
    name: Identifier = field(default_factory=Identifier)
    type: DataType = field(default_factory=DataType)
    constraints: list[ColumnConstraint] = field(default_factory=list)
    default: Expr | None = None
    identity: Node | None = None
    generated: Node | None = None
    comment: str | None = None
    collation: QualifiedName | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TableConstraint(Node):
    constraint_kind: str = ""
    name: Identifier | None = None
    columns: list[Identifier] = field(default_factory=list)
    ref_table: QualifiedName | None = None
    ref_columns: list[Identifier] = field(default_factory=list)
    expr: Expr | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CreateTable(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    columns: list[ColumnDef] = field(default_factory=list)
    constraints: list[TableConstraint] = field(default_factory=list)
    as_query: Query | None = None
    like: QualifiedName | None = None
    temporary: bool = False
    external: bool = False
    replace: bool = False
    if_not_exists: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CreateView(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    columns: list[Identifier] = field(default_factory=list)
    query: Query | None = None
    materialized: bool = False
    replace: bool = False
    secure: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CreateIndex(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    table: QualifiedName = field(default_factory=QualifiedName)
    columns: list[Expr] = field(default_factory=list)
    unique: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CreateSequence(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CreateSynonym(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    target: QualifiedName = field(default_factory=QualifiedName)
    public: bool = False


@dataclass(slots=True)
class CreateSchema(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CreateDatabase(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RoutineParam(Node):
    name: Identifier = field(default_factory=Identifier)
    mode: str | None = None
    type: DataType | None = None
    default: Expr | None = None


@dataclass(slots=True)
class CreateRoutine(Node):
    routine_kind: str = "procedure"
    name: QualifiedName = field(default_factory=QualifiedName)
    params: list[RoutineParam] = field(default_factory=list)
    returns: DataType | None = None
    language: str | None = None
    body: Block | Query | Expr | str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Alter(Node):
    object_kind: str = ""
    name: QualifiedName = field(default_factory=QualifiedName)
    actions: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class Drop(Node):
    object_kind: str = ""
    names: list[QualifiedName] = field(default_factory=list)
    if_exists: bool = False
    cascade: bool = False


@dataclass(slots=True)
class CommentOn(Node):
    object_kind: str = ""
    name: QualifiedName = field(default_factory=QualifiedName)
    text: str = ""


@dataclass(slots=True)
class Rename(Node):
    object_kind: str = ""
    name: QualifiedName = field(default_factory=QualifiedName)
    new_name: QualifiedName = field(default_factory=QualifiedName)


@dataclass(slots=True)
class Grant(Node):
    privileges: list[str] = field(default_factory=list)
    object_kind: str = ""
    object_name: QualifiedName = field(default_factory=QualifiedName)
    grantees: list[QualifiedName] = field(default_factory=list)
    with_grant_option: bool = False


@dataclass(slots=True)
class Revoke(Node):
    privileges: list[str] = field(default_factory=list)
    object_kind: str = ""
    object_name: QualifiedName = field(default_factory=QualifiedName)
    grantees: list[QualifiedName] = field(default_factory=list)
    with_grant_option: bool = False


@dataclass(slots=True)
class CreatePrincipal(Node):
    principal_kind: str = "user"
    name: QualifiedName = field(default_factory=QualifiedName)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Declare(Node):
    name: Identifier = field(default_factory=Identifier)
    type: DataType | None = None
    default: Expr | None = None


@dataclass(slots=True)
class CursorDecl(Node):
    name: Identifier = field(default_factory=Identifier)
    query: Query | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HandlerDecl(Node):
    condition: str = ""
    statements: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class ExceptionHandler(Node):
    conditions: list[str] = field(default_factory=list)
    statements: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class Block(Node):
    label: str | None = None
    declarations: list[Declare | CursorDecl | HandlerDecl] = field(default_factory=list)
    statements: list[Node] = field(default_factory=list)
    exception_handlers: list[ExceptionHandler] = field(default_factory=list)


@dataclass(slots=True)
class If(Node):
    branches: list[tuple[Expr, list[Node]]] = field(default_factory=list)
    else_: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class While(Node):
    condition: Expr | None = None
    statements: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class Loop(Node):
    label: str | None = None
    statements: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class Range(Node):
    start: Expr | None = None
    end: Expr | None = None
    step: Expr | None = None
    reverse: bool = False


@dataclass(slots=True)
class ForLoop(Node):
    var: Identifier = field(default_factory=Identifier)
    iterable: Query | Range | Expr | None = None
    statements: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class Repeat(Node):
    statements: list[Node] = field(default_factory=list)
    until: Expr | None = None


@dataclass(slots=True)
class Leave(Node):
    label: str | None = None


@dataclass(slots=True)
class Exit(Node):
    label: str | None = None


@dataclass(slots=True)
class Continue(Node):
    label: str | None = None


@dataclass(slots=True)
class Return(Node):
    expr: Expr | None = None


@dataclass(slots=True)
class Raise(Node):
    expr: Expr | None = None


@dataclass(slots=True)
class Signal(Node):
    condition: str | None = None
    expr: Expr | None = None


@dataclass(slots=True)
class Assign(Node):
    target: Expr | None = None
    value: Expr | None = None


@dataclass(slots=True)
class SetItem(Node):
    name: Expr | None = None
    value: Expr | None = None


@dataclass(slots=True)
class SetStatement(Node):
    scope: str | None = None
    items: list[SetItem | Assignment] = field(default_factory=list)


@dataclass(slots=True)
class Call(Node):
    name: QualifiedName = field(default_factory=QualifiedName)
    args: list[Expr] = field(default_factory=list)


@dataclass(slots=True)
class DynamicSql(Node):
    sql_expr: Expr | None = None
    into: list[Identifier] = field(default_factory=list)
    using: list[Expr] = field(default_factory=list)
    parsed: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class OpenCursor(Node):
    name: Identifier = field(default_factory=Identifier)
    args: list[Expr] = field(default_factory=list)


@dataclass(slots=True)
class Fetch(Node):
    name: Identifier = field(default_factory=Identifier)
    into: list[Identifier] = field(default_factory=list)


@dataclass(slots=True)
class CloseCursor(Node):
    name: Identifier = field(default_factory=Identifier)


@dataclass(slots=True)
class Commit(Node):
    chain: bool | None = None


@dataclass(slots=True)
class Rollback(Node):
    savepoint: Identifier | None = None


@dataclass(slots=True)
class Use(Node):
    database: QualifiedName = field(default_factory=QualifiedName)


@dataclass(slots=True)
class ClientCommand(Node):
    text: str = ""


@dataclass(slots=True)
class Unparsed(Node):
    text: str = ""
    reason: str = ""


@dataclass(slots=True)
class Issue:
    severity: str
    code: str
    message: str
    span: Span | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.span is not None:
            result["span"] = self.span.to_dict()
        return result


@dataclass(slots=True)
class ParseResult:
    statements: list[Node] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.statements:
            result["statements"] = [_serialize(statement) for statement in self.statements]
        if self.issues:
            result["issues"] = [_serialize(issue) for issue in self.issues]
        return result


type Expr = (
    Literal
    | ColumnRef
    | Star
    | Param
    | Variable
    | Unary
    | Binary
    | Between
    | InList
    | InSubquery
    | Like
    | IsNull
    | Exists
    | Quantified
    | Case
    | Cast
    | FunctionCall
    | Interval
    | Extract
    | Subquery
    | ArrayLiteral
    | StructLiteral
    | Subscript
    | FieldAccess
    | Collate
    | Lambda
    | DialectExpr
)
type FromItem = TableRef | SubqueryRef | TableFunctionRef | Join | ValuesRef
type Statement = Node
