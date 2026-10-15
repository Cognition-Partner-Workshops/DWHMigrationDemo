from __future__ import annotations

import json
from dataclasses import fields

from migraai.discovery import ast
from migraai.discovery.ast import (
    Binary,
    ColumnConstraint,
    ColumnRef,
    FrameBound,
    Identifier,
    Join,
    Literal,
    QualifiedName,
    RefHint,
    TableConstraint,
    walk,
)
from migraai.discovery.parser_base import ParserBase


def test_to_dict_has_stable_ordered_keys_and_omits_empty_fields() -> None:
    expression = Binary(
        op="and",
        left=Literal(type="number", value="1"),
        right=ColumnRef(
            name=QualifiedName(
                parts=[Identifier(value="sales", quoted=True), Identifier(value="amount")]
            )
        ),
    )

    encoded = json.dumps(expression.to_dict(), separators=(",", ":"))

    assert encoded == (
        '{"kind":"binary","span":{"start_line":1,"start_col":1,"end_line":1,"end_col":1},'
        '"op":"and","left":{"kind":"literal","span":{"start_line":1,"start_col":1,'
        '"end_line":1,"end_col":1},"type":"number","value":"1"},"right":{"kind":"column_ref",'
        '"span":{"start_line":1,"start_col":1,"end_line":1,"end_col":1},"name":{"kind":'
        '"qualified_name","span":{"start_line":1,"start_col":1,"end_line":1,"end_col":1},'
        '"parts":[{"kind":"identifier","span":{"start_line":1,"start_col":1,"end_line":1,'
        '"end_col":1},"value":"sales","quoted":true},{"kind":"identifier","span":{'
        '"start_line":1,"start_col":1,"end_line":1,"end_col":1},"value":"amount",'
        '"quoted":false}]}}}'
    )


def test_walk_yields_nodes_depth_first() -> None:
    expression = Binary(
        op="+",
        left=Literal(type="number", value="2"),
        right=ColumnRef(name=QualifiedName(parts=[Identifier(value="price")])),
    )

    assert [node.kind for node in walk(expression)] == [
        "binary",
        "literal",
        "column_ref",
        "qualified_name",
        "identifier",
    ]


def test_domain_kinds_do_not_shadow_the_node_discriminator() -> None:
    for node_type in (FrameBound, Join, RefHint, ColumnConstraint, TableConstraint):
        assert "kind" not in {item.name for item in fields(node_type)}
    assert FrameBound(bound_type="current_row").kind == "frame_bound"
    assert Join(join_type="left").kind == "join"
    assert RefHint(ref_kind="reads").kind == "ref_hint"
    assert ColumnConstraint(constraint_kind="foreign_key").kind == "column_constraint"
    assert TableConstraint(constraint_kind="foreign_key").kind == "table_constraint"


def test_new_ast_fields_default_without_changing_default_serialization() -> None:
    assert ast.Query().distribute_by == []
    assert ast.Query().sort_by == []
    assert ast.Query().cluster_by == []
    assert ast.StructField().comment is None
    assert ast.Grant().grantee_kinds == []
    assert ast.Revoke().grantee_kinds == []
    assert ast.Select().hierarchical is None
    assert ast.Select().into_list == []
    assert ast.Select().bulk_collect is False
    assert ast.Fetch().bulk_collect is False
    assert ast.Fetch().limit is None
    for node in (ast.Insert(), ast.Update(), ast.Delete(), ast.Merge()):
        assert node.top is None
        assert node.output == []
        assert node.output_into is None
        assert node.target_variable is None
    assert ast.While().label is None
    assert ast.Repeat().label is None
    assert ast.ForLoop().label is None
    assert ast.ForLoop().cursor is None
    assert ast.DialectExpr().ref_hints == []
    assert ast.Cast().function_name is None

    assert "bulk_collect" not in ast.Select().to_dict()
    assert "bulk_collect" not in ast.Fetch().to_dict()
    assert "default_schema" not in ast.ParseResult().to_dict()


def test_parse_result_includes_the_current_default_schema() -> None:
    result = ParserBase().parse("USE first_db; USE second_db")

    assert result.default_schema == "second_db"
    assert result.to_dict()["default_schema"] == "second_db"
