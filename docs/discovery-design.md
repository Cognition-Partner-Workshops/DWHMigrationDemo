# Discovery & parsing design (Phase 3)

This is the shared contract for every Phase 3 workstream. The core (W1) owns everything here except the dialect and ETL modules. Parser groups code only against the interfaces below. If you need to change one of them, raise it with the lead; don't edit the core.

## 1. Package layout and ownership

```
backend/migraai/discovery/
  ast.py           W1  AST node dataclasses (§3) + to_dict()/walk()
  lexer.py         W1  configurable lexer (§2)
  parser_base.py   W1  token cursor, Pratt expression parser, ANSI statement/query/DDL parsing (§4)
  references.py    W1  generic reference extraction from the AST (§6)
  objects.py       W1  statements → ParsedObject inventory entries (§5)
  detect.py        W1  file-type detection
  uploads.py       W1  streaming upload, ZIP safety, encryption
  resolver.py      W1  name resolution, external nodes
  inventory.py     W1  in-memory SQLite inventory + graph queries
  registry.py      W1  fixed dialect/ETL module map (§7)
  parsers/<dialect>/   W2–W4  one package per dialect, exporting `Parser`
backend/migraai/etl/<tool>/parse.py   W5  exporting `parse(data: bytes, rel_path: str) -> EtlParseResult`
backend/tests/unit/parsers/<dialect>/ and backend/tests/unit/etl/<tool>/   owned by the group
testpacks/<source>/ and testpacks/etl/<tool>/                              owned by the group
```

Dialect ids: `teradata, oracle, sqlserver, snowflake, synapse, db2, hive, netezza`.
ETL ids: `informatica, datastage, ssis, airflow, controlm, shell, python, spark`.

## 2. Lexer

`Token(kind: TokenKind, text: str, value: str, line: int, col: int, offset: int)`, where `line` and `col` are 1-based.

`TokenKind` values:
- `IDENT` — also used for keywords; the parser decides by case-insensitive comparison
- `QUOTED_IDENT` — `value` is the unquoted text
- `NUMBER`
- `STRING` — `value` is the unescaped text
- `NATIONAL_STRING`, `BIT_STRING`, `HEX_STRING`
- `PARAM` — `?`, `:name`, `$1`, `@name`, `@@name`, `&name`, `${hivevar:x}`
- `OPERATOR`, `PUNCT`
- `DOLLAR_BODY` — `$$...$$`
- `DOT_COMMAND` — a whole BTEQ or SQL*Plus line starting with `.` or a client command
- `TERMINATOR`, `EOF`

Comments and whitespace are dropped from the token stream. Comment spans are kept in `Lexer.comments` for previews.

`LexerConfig` is a frozen dataclass that each dialect instantiates:
- `ident_quotes`: e.g. `{'"': '"'}`, `{'[': ']', '"': '"'}` or `` {'`': '`'} ``
- `string_escapes`: `"double"` or `"backslash"`
- Oracle `q'[...]'` strings
- nested block comments
- dollar quoting
- `batch_separators`: whole-line regexes such as `^\s*GO\s*$` or `^\s*/\s*$`, which become `TERMINATOR`
- `statement_terminators`: default `;`; Db2 scripts may use `@`
- `dot_command_prefixes`
- `param_styles`
- `extra_operators`: e.g. `::`, `||`, `(+)`, `=>`, `**`, `^=`, `!=`, `<=>`

The lexer never raises. A bad character becomes an `OPERATOR` token and an issue.

## 3. AST

All nodes are `@dataclass(slots=True)` subclasses of `Node`. Every node has:
- `span: Span(start_line, start_col, end_line, end_col)`
- `kind: ClassVar[str]`, the class name in snake_case
- No AST node field may be named `kind`; reserve it for the snake_case node discriminator.

`to_dict()` gives stable JSON with keys in order and `None` or empty fields omitted. It is used for golden tests and as Phase 4 context. `walk(node)` yields nodes depth-first. Identifiers are `Identifier(value, quoted)` and names are `QualifiedName(parts: list[Identifier])`.

**Expressions**
- `Literal(type: "number"|"string"|"national_string"|"boolean"|"null"|"date"|"time"|"timestamp"|"interval"|"hex"|"bit", value: str)`
- `ColumnRef(name: QualifiedName)`, `Star(qualifier: QualifiedName|None, except_: list[Identifier], replace: list[Alias])`
- `Param(text)`, `Variable(name)`
- `Unary(op, operand)`, `Binary(op, left, right)`. `op` is normalised lowercase, e.g. `and`, `or`, `=`, `<>`, `||`, `+`, `like`, `rlike`, `regexp`, `is distinct from`.
- `Between(expr, low, high, negated)`, `InList(expr, items, negated)`, `InSubquery(expr, query, negated)`
- `Like(expr, pattern, escape, op: like|ilike|similar, negated, quantifier: any|all|None)`
- `IsNull(expr, negated)`, `Exists(query, negated)`, `Quantified(op, quantifier, query)`
- `Case(operand, whens: list[When(cond, result)], else_)`
- `Cast(expr, to: DataType, style: "cast"|"try_cast"|"safe_cast"|"double_colon"|"convert", format: Expr|None)`
- `FunctionCall(name: QualifiedName, args: list[Expr|NamedArg], distinct, star, filter, within_group: list[OrderItem], over: WindowSpec|None, ignore_nulls: bool|None)`
- `WindowSpec(name, partition_by, order_by, frame: Frame|None)`, `Frame(unit, start, end, exclude)`, `FrameBound(bound_type, value)`
- `Interval(value, unit, to_unit)`, `Extract(field, expr)`, `Subquery(query)`
- `ArrayLiteral(items)`, `StructLiteral(fields)`, `Subscript(expr, index)`, `FieldAccess(expr, field)`, `Collate(expr, collation)`, `Lambda(params, body)`
- `DialectExpr(name: str, args: dict[str, Node|list|str|None])` — the single extension point for dialect-only expression forms such as Oracle `(+)`, `PRIOR` and `CONNECT_BY_ROOT`, the Teradata `(FORMAT 'x')` and `(TITLE ...)` attributes, and Snowflake `:path` access. Each dialect documents its names in its package README.

**Queries**
- `Query(with_: With|None, body: Select|SetOperation|Values|Subquery, order_by, limit: Limit|None)`
- `With(recursive, ctes: list[Cte(name, columns, query, materialized)])`
- `Select(distinct, distinct_on, top: Top|None, projections: list[SelectItem(expr, alias)], into, from_: list[FromItem], where, group_by: GroupBy|None, having, qualify, windows, sample, hints: list[str])`
- `SetOperation(op: union|intersect|except|minus, all, left, right)`
- `Limit(count, offset, fetch_first, with_ties, percent)`, `Top(count, percent, with_ties)`
- `GroupBy(items, rollup/cube/grouping_sets as nested nodes, all)`
- `OrderItem(expr, desc, nulls)`
- From items:
  - `TableRef(name, alias, column_aliases, temporal: DialectExpr|None, sample, hints)`
  - `SubqueryRef(query, alias, lateral)`
  - `TableFunctionRef(call, alias, lateral)`
  - `Join(join_type: inner|left|right|full|cross|semi|anti|natural, left, right, on, using)`
  - `Pivot`/`Unpivot` as `DialectExpr` on the `TableRef`
  - `ValuesRef`

**DML**
- `Insert(target, columns, source: Query|Values|DefaultValues, overwrite, partition, conflict)`
- `Update(target, alias, assignments: list[Assignment(target, value)], from_, where)`
- `Delete(target, alias, using, where)`
- `Merge(target, alias, source, on, clauses: list[MergeClause(matched, condition, action)])`
- `Truncate(target)`, `Copy`/`Load` as `DialectStatement`

**DDL**
- `DataType(name, params: list[Expr|str], children: list[DataType|StructField], array_dims)`
- `ColumnDef(name, type, constraints: list[ColumnConstraint], default, identity, generated, comment, collation, options: dict)`, `ColumnConstraint(constraint_kind, name, columns, ref_table, ref_columns, expr, options)`
- `TableConstraint(constraint_kind: primary_key|unique|foreign_key|check, name, columns, ref_table, ref_columns, expr, options)`
- `CreateTable(name, columns, constraints, as_query, like, temporary, external, replace, if_not_exists, options: dict[str, Node|str|list])` — `options` holds dialect properties such as `primary_index`, `distribution`, `partitioned_by`, `stored_as`, `cluster_by`, `organize_on` and `set_multiset`
- `CreateView(name, columns, query, materialized, replace, secure, options)`
- `CreateIndex(name, table, columns, unique, options)`
- `CreateSequence`, `CreateSynonym(name, target, public)`, `CreateSchema`, `CreateDatabase`
- `CreateRoutine(routine_kind: procedure|function|macro|trigger|package|package_body|type, name, params: list[RoutineParam(name, mode, type, default)], returns, language, body: Block|Query|Expr|str|None, options)` — the body is parsed into a `Block` when the language is SQL-like; an external language body stays a `str` with `language` set
- `Alter(object_kind, name, actions: list[Node])`, `Drop(object_kind, names, if_exists, cascade)`, `CommentOn`, `Rename`

**Security**
- `Grant(privileges, object_kind, object_name, grantees, with_grant_option)`, `Revoke(...)`
- `CreatePrincipal(principal_kind: user|role|login|group, name, options)`
- Policies, masks, row permissions and row-access policies are `DialectStatement`s with `references: list[RefHint(name, ref_kind)]`

**Procedural**
- `Block(label, declarations: list[Declare|CursorDecl|HandlerDecl], statements, exception_handlers: list[ExceptionHandler(conditions, statements)])`
- `If(branches: list[(cond, statements)], else_)`
- `While`, `Loop`, `ForLoop(var, iterable: Query|Range, statements)`, `Repeat`
- `Leave`/`Exit`/`Continue`/`Return(expr)`/`Raise`/`Signal`, all with `(label|expr)`
- `Assign(target, value)`, `SetStatement(scope, items)`
- `Call(name, args)` — covers CALL, EXEC and EXECUTE proc
- `DynamicSql(sql_expr, into, using)` — `EXECUTE IMMEDIATE` or `sp_executesql`; a literal string argument is also parsed, and the result kept in `parsed: list[Statement]`
- `OpenCursor`, `Fetch`, `CloseCursor`, `Commit`, `Rollback`, `Use(database)`

**Fallbacks**
- `DialectStatement(name: str, args: dict, references: list[RefHint])` — any platform-only statement (Snowflake stage, pipe, stream and task; Teradata join index and COLLECT STATISTICS; Synapse external data source). It must still be fully parsed into `args`.
- `ClientCommand(text)` — BTEQ or SQL*Plus dot-commands
- `Unparsed(text, reason)` — produced only by error recovery

`ParseResult(statements: list[Node], issues: list[Issue])`
`Issue(severity: info|warning|error, code: str, message: str, span: Span|None)`

## 4. Parser base

`ParserBase(tokens, config)` provides:
- Cursor helpers: `peek(n)`, `at_keyword(*words)`, `accept_keyword(*words)`, `expect_keyword(*words)`, `accept(kind, text=None)`, `expect(...)`, `parse_identifier()`, `parse_qualified_name()`, `parse_comma_list(fn)`.
- A **Pratt expression parser**: `parse_expr(min_bp=0)`, driven by an overridable operator table `binary_ops: dict[str, (lbp, rbp, op_name)]`. Hooks: `parse_primary_ext()`, `parse_postfix_ext(left)` and `parse_statement_ext()`, each returning `None` to fall through.
- ANSI parsing of queries, DML, the DDL above, GRANT/REVOKE, and procedural blocks in SQL/PSM style.

**Recovery:** a `ParseError` inside a statement records an `Issue`. The parser then skips to the next `TERMINATOR` at depth 0, or to a statement-starting keyword at line start, and emits `Unparsed`. A statement containing `Unparsed` sub-nodes is `partial`.

**Limits:**
- Maximum expression depth is 500. Going over gives an `error` issue and that statement becomes `Unparsed`.
- Each file has a 30-second budget, checked every 1,000 tokens. Hitting it gives an `error` issue, and the rest of the file becomes one `Unparsed` node.

`Parser` (exported by each dialect package): `Parser().parse(text: str, rel_path: str) -> ParseResult`. A dialect subclasses `ParserBase`, sets its `LexerConfig`, extends the keyword and operator tables, and overrides the hooks or `parse_<construct>` methods. `sqlserver` exports `TSqlParserBase` for `synapse` to subclass.

## 5. Objects (inventory entries)

`objects.extract(result, rel_path, dialect) -> list[ParsedObject]`

`ParsedObject(type, schema, name, qualified_name, start_line, end_line, status: parsed|partial|unparsed, references: list[Reference], issues, node: Node)`

- `type` is one of the canonical object types used by the KB catalogs, such as table, view, materialized_view, procedure, function, macro, package, package_body, trigger, sequence, synonym, index, join_index, role, user, grant, stage, pipe, stream, task, policy and external_table.
- Each `Create*` statement, and each `DialectStatement` with an `object_type` arg, becomes one object.
- Top-level DML and query statements in a file that defines no objects become a single `script` object for that file.
- Grants become `grant` objects named `<privileges> on <object>`.

## 6. References

`references.extract(node, dialect) -> list[Reference]`

`Reference(name: QualifiedName, ref_kind, span)`, where `ref_kind` is one of:
- `reads` — `TableRef` in a query
- `writes` — DML or CTAS target
- `calls` — `Call`, a function call to a non-builtin name, or the `parsed` contents of `DynamicSql`
- `foreign_key`
- `grant_on`
- `synonym_for`
- `index_on`
- `trigger_on`
- `defined_in` — package body to package
- `etl_reads`, `etl_writes`, `etl_runs`, `schedules`

Extraction is generic over the AST. Dialects add only `DialectStatement.references`. CTE names and aliases are scoped out, so they aren't references. Function calls count as references only when the name isn't in the dialect's `builtin_functions` set.

## 7. Registry, ETL contract and resolution

- `registry.SQL_PARSERS` maps each dialect id to `"migraai.discovery.parsers.<id>"`; `registry.ETL_PARSERS` maps each ETL id to `"migraai.etl.<id>.parse"`. Modules are imported lazily. A missing module makes those files "Unsupported type" with an issue, so groups can land in any order.
- ETL `parse(data, rel_path) -> EtlParseResult(objects: list[ParsedObject], embedded_sql: list[EmbeddedSql(owner_qualified_name, text, location: str, dialect_hint: str|None)], issues)`.
  - The core parses each `EmbeddedSql` with the confirmed source dialect, or with `dialect_hint` if it names a registered dialect. The resulting references are added to the owner as `etl_reads` and `etl_writes`.
  - All XML is parsed with `defusedxml`; Python with `ast`; nothing is ever executed or imported.
- **Resolution** (`resolver.py`) uses the dialect's `case_rule` (`upper`, `lower` or `insensitive`) and `default_schema` (the last `USE`, `SET SCHEMA` or `DATABASE` statement in the file).
  - An unqualified name matches if exactly one object with that name exists.
  - If several objects match, the reference gets an `ambiguous_reference` warning.
  - If none match, the reference resolves to an `external` node.

## 8. API shapes (for the UI)

```
UploadRow        {id, rel_path, size, detected_type, status: pending|parsing|parsed|partial|unparsed|unsupported|rejected, message, selected, object_count}
DiscoveryStatus  {state: idle|running|done|failed, files_total, files_done, objects, issues, external_refs}
InventoryItem    {id, type, schema, name, qualified_name, upload_id, rel_path, start_line, end_line, status, issue_count, upstream_count, downstream_count, external}
InventoryPage    {items: InventoryItem[], total, page, page_size}
InventoryDetail  InventoryItem + {issues: Issue[], upstream: Edge[], downstream: Edge[], source_preview: string}
Edge             {object_id, qualified_name, type, kind, external}
Graph            {nodes: [{id, label, type, status, external, center}], edges: [{source, target, kind}], truncated}
```

- `source_preview` is at most 200 lines and is returned as plain text; the UI escapes it.
- `GET /api/graph?object_id=&depth=1..3` caps the result at 300 nodes and sets `truncated` when it hits the cap.
- `GET /api/resources` adds `count` and `auto_available` to each item.

## Appendix: How to add a dialect

1. Add `migraai.discovery.parsers.<dialect>` and export `Parser`, subclassing `ParserBase`.
2. Set the dialect's `LexerConfig`, `case_rule`, `default_schema`, and `builtin_functions`.
3. Override `parse_primary_ext()`, `parse_postfix_ext(left)`, or `parse_statement_ext()` for dialect syntax. Return `None` to fall through to the base parser; extend `binary_ops` with entries shaped as `(left_binding_power, right_binding_power, operator_name)`.
4. Add the dialect id to `registry.SQL_PARSERS`, unit tests, and a golden test pack.

The `ansi` package is the reference: it uses `ParserBase` with standard quoted-identifier, Oracle q-quote, nested-comment, and dollar-body lexer options. Test packs live under `backend/testpacks/<id>/`; `expected_inventory.yaml` contains exact objects and edges. An optional `pack.yaml` can specify `kind: sql` or `kind: etl` and `source_dialect: <dialect id>`. `kind` defaults to `sql`; SQL packs default their source dialect to the pack id, while ETL packs must set `source_dialect` (typically `ansi` for embedded SQL).

## 9. Test packs

- `testpacks/<id>/` holds inter-related sources, and `testpacks/<id>/expected_inventory.yaml` lists objects `{type, qualified_name, status}` and edges `{from, to, kind}`.
- `backend/tests/golden/test_testpacks.py` (owned by W1) discovers each pack and asserts an exact match on objects and edges.
- AST golden files go in `backend/tests/unit/parsers/<dialect>/golden/*.json`, made with `to_dict()`.
- Each pack must include:
  - at least 25 objects with cross-file dependencies
  - a broken statement that recovers
  - an unsupported or unknown construct
  - a reference to an object that wasn't uploaded
