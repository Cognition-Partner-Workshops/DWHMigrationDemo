"""Convert CPython ``ast`` nodes into MigraAI AST nodes.

Nothing is ever executed: this is a pure structural conversion. Every
supported construct maps to a shared node; Python-only forms become
``DialectExpr("python.<name>")`` nodes so no source text is stored for a
supported construct.
"""

from __future__ import annotations

import ast as pyast
from dataclasses import dataclass, field
from typing import Any

from migraai.discovery import ast
from migraai.etl.python.common import redact, span

SECRET_KEYWORDS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_token",
        "private_key",
        "credentials",
    }
)

_BIN_OPS = {
    pyast.Add: "+",
    pyast.Sub: "-",
    pyast.Mult: "*",
    pyast.Div: "/",
    pyast.FloorDiv: "//",
    pyast.Mod: "%",
    pyast.Pow: "**",
    pyast.LShift: "<<",
    pyast.RShift: ">>",
    pyast.BitOr: "|",
    pyast.BitAnd: "&",
    pyast.BitXor: "^",
    pyast.MatMult: "@",
}
_CMP_OPS = {
    pyast.Eq: "==",
    pyast.NotEq: "!=",
    pyast.Lt: "<",
    pyast.LtE: "<=",
    pyast.Gt: ">",
    pyast.GtE: ">=",
    pyast.In: "in",
    pyast.NotIn: "not in",
    pyast.Is: "is",
    pyast.IsNot: "is not",
}
_UNARY_OPS = {pyast.Not: "not", pyast.USub: "-", pyast.UAdd: "+", pyast.Invert: "~"}

MAX_DEPTH = 500


@dataclass(slots=True)
class ConvertContext:
    tool: str = "python"
    issues: list[ast.Issue] = field(default_factory=list)
    depth: int = 0

    def error(self, message: str, node: pyast.AST) -> ast.Issue:
        issue = ast.Issue(
            "error",
            f"{self.tool}.convert",
            message,
            _pyspan(node),
        )
        self.issues.append(issue)
        return issue


def _pyspan(node: pyast.AST) -> ast.Span:
    return span(
        getattr(node, "lineno", 1),
        getattr(node, "end_lineno", None) or getattr(node, "lineno", 1),
        getattr(node, "col_offset", 0) + 1,
        getattr(node, "end_col_offset", 1) or 1,
    )


def _dotted(node: pyast.AST) -> str | None:
    """Return the dotted name for a Name/Attribute chain, else None."""
    parts: list[str] = []
    while isinstance(node, pyast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, pyast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def convert_expr(node: pyast.expr, ctx: ConvertContext) -> ast.Node:
    ctx.depth += 1
    try:
        if ctx.depth > MAX_DEPTH:
            ctx.error("Expression depth limit exceeded.", node)
            return ast.Unparsed(text="", reason="Expression depth limit exceeded.")
        return _convert(node, ctx)
    finally:
        ctx.depth -= 1


def _convert(node: pyast.expr, ctx: ConvertContext) -> ast.Node:
    sp = _pyspan(node)
    if isinstance(node, pyast.Constant):
        value = node.value
        if isinstance(value, str):
            return ast.Literal(type="string", value=redact(value), span=sp)
        if isinstance(value, bytes):
            return ast.Literal(type="hex", value=value.hex(), span=sp)
        if isinstance(value, bool):
            return ast.Literal(type="boolean", value=str(value), span=sp)
        if value is None:
            return ast.Literal(type="null", value="None", span=sp)
        if isinstance(value, (int, float, complex)):
            return ast.Literal(type="number", value=str(value), span=sp)
        return ast.Literal(type="string", value=repr(value), span=sp)
    if isinstance(node, pyast.Name):
        return ast.Variable(name=node.id, span=sp)
    if isinstance(node, pyast.Attribute):
        return ast.FieldAccess(
            expr=convert_expr(node.value, ctx),
            field=ast.Identifier(value=node.attr, span=sp),
            span=sp,
        )
    if isinstance(node, pyast.Call):
        return _convert_call(node, ctx)
    if isinstance(node, pyast.BinOp):
        return ast.Binary(
            op=_BIN_OPS.get(type(node.op), type(node.op).__name__.lower()),
            left=convert_expr(node.left, ctx),
            right=convert_expr(node.right, ctx),
            span=sp,
        )
    if isinstance(node, pyast.BoolOp):
        op = "and" if isinstance(node.op, pyast.And) else "or"
        values = [convert_expr(value, ctx) for value in node.values]
        result = values[0]
        for value in values[1:]:
            result = ast.Binary(op=op, left=result, right=value, span=sp)
        return result
    if isinstance(node, pyast.UnaryOp):
        return ast.Unary(
            op=_UNARY_OPS.get(type(node.op), type(node.op).__name__.lower()),
            operand=convert_expr(node.operand, ctx),
            span=sp,
        )
    if isinstance(node, pyast.Compare):
        comparisons = [node.left, *node.comparators]
        pieces: list[ast.Node] = []
        for index, op in enumerate(node.ops):
            pieces.append(
                ast.Binary(
                    op=_CMP_OPS.get(type(op), type(op).__name__.lower()),
                    left=convert_expr(comparisons[index], ctx),
                    right=convert_expr(comparisons[index + 1], ctx),
                    span=sp,
                )
            )
        result = pieces[0]
        for piece in pieces[1:]:
            result = ast.Binary(op="and", left=result, right=piece, span=sp)
        return result
    if isinstance(node, pyast.IfExp):
        return ast.Case(
            whens=[
                ast.When(
                    cond=convert_expr(node.test, ctx),
                    result=convert_expr(node.body, ctx),
                    span=sp,
                )
            ],
            else_=convert_expr(node.orelse, ctx),
            span=sp,
        )
    if isinstance(node, pyast.JoinedStr):
        parts: list[ast.Node] = []
        for value in node.values:
            if isinstance(value, pyast.Constant):
                parts.append(ast.Literal(type="string", value=redact(str(value.value))))
            else:
                parts.append(_convert_formatted(value, ctx))
        return ast.DialectExpr(name="python.fstring", args={"parts": parts}, span=sp)
    if isinstance(node, pyast.FormattedValue):
        return _convert_formatted(node, ctx)
    if isinstance(node, (pyast.List, pyast.Tuple, pyast.Set)):
        return ast.ArrayLiteral(items=[convert_expr(item, ctx) for item in node.elts], span=sp)
    if isinstance(node, pyast.Dict):
        if all(isinstance(key, pyast.Constant) and isinstance(key.value, str) for key in node.keys):
            return ast.StructLiteral(
                fields=[
                    ast.StructField(
                        name=ast.Identifier(value=key.value),
                        value=convert_expr(value, ctx),
                        span=_pyspan(value),
                    )
                    for key, value in zip(node.keys, node.values, strict=True)
                ],
                span=sp,
            )
        return ast.DialectExpr(
            name="python.dict",
            args={
                "keys": [
                    convert_expr(key, ctx) if key is not None else ast.Literal(type="null")
                    for key in node.keys
                ],
                "values": [convert_expr(value, ctx) for value in node.values],
            },
            span=sp,
        )
    if isinstance(node, pyast.Subscript):
        return ast.Subscript(
            expr=convert_expr(node.value, ctx),
            index=convert_expr(node.slice, ctx),
            span=sp,
        )
    if isinstance(node, pyast.Slice):
        return ast.DialectExpr(
            name="python.slice",
            args={
                "lower": convert_expr(node.lower, ctx) if node.lower else None,
                "upper": convert_expr(node.upper, ctx) if node.upper else None,
                "step": convert_expr(node.step, ctx) if node.step else None,
            },
            span=sp,
        )
    if isinstance(node, pyast.Lambda):
        params = [ast.Identifier(value=arg.arg) for arg in node.args.args]
        return ast.Lambda(params=params, body=convert_expr(node.body, ctx), span=sp)
    if isinstance(node, pyast.Starred):
        return ast.DialectExpr(
            name="python.starred", args={"value": convert_expr(node.value, ctx)}, span=sp
        )
    name = type(node).__name__.lower()
    children: dict[str, Any] = {}
    for field_name in node._fields:
        value = getattr(node, field_name, None)
        children[field_name] = _convert_child(value, ctx)
    return ast.DialectExpr(name=f"python.{name}", args=children, span=sp)


def _convert_child(value: Any, ctx: ConvertContext) -> Any:
    if isinstance(value, pyast.expr):
        return convert_expr(value, ctx)
    if isinstance(value, pyast.AST):
        children = {
            field_name: _convert_child(getattr(value, field_name, None), ctx)
            for field_name in value._fields
        }
        return ast.DialectExpr(
            name=f"python.{type(value).__name__.lower()}",
            args=children,
            span=_pyspan(value),
        )
    if isinstance(value, list):
        return [_convert_child(item, ctx) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return None


def _convert_formatted(node: pyast.FormattedValue, ctx: ConvertContext) -> ast.Node:
    return ast.DialectExpr(
        name="python.formatted",
        args={
            "value": convert_expr(node.value, ctx),
            "conversion": str(node.conversion) if node.conversion and node.conversion > 0 else None,
            "format_spec": convert_expr(node.format_spec, ctx) if node.format_spec else None,
        },
        span=_pyspan(node),
    )


def _convert_call(node: pyast.Call, ctx: ConvertContext) -> ast.Node:
    sp = _pyspan(node)
    args: list[ast.Node] = []
    for arg in node.args:
        if isinstance(arg, pyast.Starred):
            args.append(
                ast.DialectExpr(
                    name="python.starred",
                    args={"value": convert_expr(arg.value, ctx)},
                    span=_pyspan(arg),
                )
            )
        else:
            args.append(convert_expr(arg, ctx))
    for keyword in node.keywords:
        if keyword.arg is None:
            args.append(
                ast.DialectExpr(
                    name="python.double_starred",
                    args={"value": convert_expr(keyword.value, ctx)},
                    span=_pyspan(keyword.value),
                )
            )
            continue
        if keyword.arg.casefold() in SECRET_KEYWORDS:
            value = ast.Literal(type="string", value="****", span=_pyspan(keyword.value))
        else:
            value = convert_expr(keyword.value, ctx)
        args.append(
            ast.NamedArg(
                name=ast.Identifier(value=keyword.arg),
                value=value,
                span=_pyspan(keyword.value),
            )
        )
    dotted = _dotted(node.func)
    if dotted is not None:
        from migraai.etl.python.common import qname

        return ast.FunctionCall(name=qname(dotted), args=args, span=sp)
    return ast.DialectExpr(
        name="python.call",
        args={"func": convert_expr(node.func, ctx), "args": args},
        span=sp,
    )


def convert_statement(node: pyast.stmt, ctx: ConvertContext) -> ast.Node:
    """Convert a Python statement to a shared/dialect node (best effort)."""
    sp = _pyspan(node)
    name = type(node).__name__.lower()
    if isinstance(node, pyast.Expr):
        return ast.DialectExpr(
            name="python.expr_stmt", args={"value": convert_expr(node.value, ctx)}, span=sp
        )
    if isinstance(node, pyast.Assign):
        value = convert_expr(node.value, ctx)
        targets = [convert_expr(target, ctx) for target in node.targets]
        result: ast.Node = ast.Assign(target=targets[0], value=value, span=sp)
        for target in targets[1:]:
            result = ast.Assign(target=target, value=result, span=sp)
        return result
    if isinstance(node, (pyast.AnnAssign, pyast.AugAssign)):
        return ast.Assign(
            target=convert_expr(node.target, ctx),
            value=convert_expr(node.value, ctx) if node.value else None,
            span=sp,
        )
    if isinstance(node, pyast.Return):
        return ast.Return(expr=convert_expr(node.value, ctx) if node.value else None, span=sp)
    if isinstance(node, pyast.Delete):
        return ast.DialectStatement(
            name="python.delete",
            args={"targets": [convert_expr(t, ctx) for t in node.targets]},
            span=sp,
        )
    if isinstance(node, pyast.If):
        branches = [(convert_expr(node.test, ctx), _body(node.body, ctx))]
        orelse = node.orelse
        while len(orelse) == 1 and isinstance(orelse[0], pyast.If):
            branches.append((convert_expr(orelse[0].test, ctx), _body(orelse[0].body, ctx)))
            orelse = orelse[0].orelse
        return ast.If(branches=branches, else_=_body(orelse, ctx), span=sp)
    if isinstance(node, (pyast.For, pyast.AsyncFor)):
        return ast.ForLoop(
            var=_target_name(node.target),
            iterable=convert_expr(node.iter, ctx),
            statements=_body(node.body, ctx),
            span=sp,
        )
    if isinstance(node, pyast.While):
        return ast.While(
            condition=convert_expr(node.test, ctx),
            statements=_body(node.body, ctx),
            span=sp,
        )
    if isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
        return ast.DialectStatement(
            name="python.function",
            args={
                "name": ast.Identifier(value=node.name),
                "params": [
                    ast.RoutineParam(
                        name=ast.Identifier(value=arg.arg),
                        default=None,
                    )
                    for arg in node.args.args
                ],
            },
            span=sp,
        )
    if isinstance(node, pyast.ClassDef):
        return ast.DialectStatement(
            name="python.class",
            args={"name": ast.Identifier(value=node.name)},
            span=sp,
        )
    if isinstance(node, pyast.Import):
        return ast.DialectStatement(
            name="python.import",
            args={"modules": [ast.Identifier(value=alias.name) for alias in node.names]},
            span=sp,
        )
    if isinstance(node, pyast.ImportFrom):
        return ast.DialectStatement(
            name="python.import",
            args={
                "module": ast.Identifier(value=node.module or ""),
                "names": [ast.Identifier(value=alias.name) for alias in node.names],
                "level": str(node.level),
            },
            span=sp,
        )
    if isinstance(node, (pyast.With, pyast.AsyncWith)):
        return ast.DialectStatement(
            name="python.with",
            args={
                "items": [
                    ast.DialectExpr(
                        name="python.with_item",
                        args={
                            "context": convert_expr(item.context_expr, ctx),
                            "as": _target_name(item.optional_vars) if item.optional_vars else None,
                        },
                    )
                    for item in node.items
                ],
                "body": _body(node.body, ctx),
            },
            span=sp,
        )
    if isinstance(node, pyast.Try):
        return ast.DialectStatement(
            name="python.try",
            args={
                "body": _body(node.body, ctx),
                "handlers": [
                    ast.DialectExpr(
                        name="python.except",
                        args={"body": _body(handler.body, ctx)},
                    )
                    for handler in node.handlers
                ],
            },
            span=sp,
        )
    children: dict[str, Any] = {}
    for field_name in node._fields:
        value = getattr(node, field_name, None)
        if isinstance(value, pyast.expr):
            children[field_name] = convert_expr(value, ctx)
        elif isinstance(value, list):
            children[field_name] = [
                convert_statement(item, ctx)
                if isinstance(item, pyast.stmt)
                else convert_expr(item, ctx)
                if isinstance(item, pyast.expr)
                else None
                for item in value
            ]
        elif isinstance(value, (str, int)) or value is None:
            children[field_name] = value
    return ast.DialectStatement(name=f"python.{name}", args=children, span=sp)


def _body(stmts: list[pyast.stmt], ctx: ConvertContext) -> list[ast.Node]:
    return [convert_statement(stmt, ctx) for stmt in stmts]


def _target_name(node: pyast.AST) -> ast.Identifier:
    if isinstance(node, pyast.Name):
        return ast.Identifier(value=node.id, span=_pyspan(node))
    return ast.Identifier(value="", span=_pyspan(node))


# ---------------------------------------------------------------------------
# String constant resolution (for SQL / table-name arguments)
# ---------------------------------------------------------------------------


def resolve_string(
    node: pyast.expr,
    constants: dict[str, str],
    *,
    params: list[str] | None = None,
) -> str | None:
    """Resolve a Python expression to a literal string, or return None.

    Supports literals, implicit/+ concatenation of constants, simple constant
    names, textwrap.dedent(...), sqlalchemy text(...), f-strings (expressions
    become :name placeholders) and "...".format(...) with literal patterns.
    """
    params = params if params is not None else []
    if isinstance(node, pyast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, pyast.Name):
        return constants.get(node.id)
    if isinstance(node, pyast.BinOp) and isinstance(node.op, pyast.Add):
        left = resolve_string(node.left, constants, params=params)
        right = resolve_string(node.right, constants, params=params)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, pyast.JoinedStr):
        parts: list[str] = []
        counter = len(params)
        for value in node.values:
            if isinstance(value, pyast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, pyast.FormattedValue):
                inner = value.value
                placeholder: str | None = None
                if isinstance(inner, pyast.Name):
                    placeholder = inner.id
                else:
                    counter += 1
                    placeholder = f"p{counter}"
                params.append(placeholder)
                parts.append(f":{placeholder}")
            else:
                return None
        return "".join(parts)
    if isinstance(node, pyast.Call):
        dotted = _dotted(node.func)
        if dotted in {"textwrap.dedent", "dedent", "sqlalchemy.text", "text", "sqlparse.format"}:
            if len(node.args) == 1:
                return resolve_string(node.args[0], constants, params=params)
            return None
        if dotted is None and isinstance(node.func, pyast.Attribute) and node.func.attr == "format":
            pattern = resolve_string(node.func.value, constants, params=params)
            if pattern is None:
                return None
            result = pattern
            for index, arg in enumerate(node.args):
                literal = resolve_string(arg, constants, params=params)
                if literal is None:
                    return None
                result = result.replace("{" + str(index) + "}", literal)
            for keyword in node.keywords:
                literal = resolve_string(keyword.value, constants, params=params)
                if literal is None:
                    return None
                result = result.replace("{" + keyword.arg + "}", literal)
            if "{" in result and "}" in result:
                # remaining {name} placeholders become :name params
                import re as _re

                def _ph(match: _re.Match[str]) -> str:
                    params.append(match.group(1))
                    return ":" + match.group(1)

                result = _re.sub(r"\{(\w+)\}", _ph, result)
            return result
        return None
    return None


__all__ = [
    "SECRET_KEYWORDS",
    "ConvertContext",
    "convert_expr",
    "convert_statement",
    "resolve_string",
]
