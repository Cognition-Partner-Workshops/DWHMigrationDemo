# python ETL parser

`parse(data, rel_path) -> EtlParseResult` for `.py` files that are not Airflow
DAGs or pyspark jobs (those delegate: see Routing below).

## Objects
- `module` — qualified by the dotted module path (rel_path minus `.py`,
  `/` → `.`, trailing `__init__` dropped). Node: `DialectStatement("python.module")`.
- `function` — `<module>.<func>`; methods `<module>.<Class>.<method>`; nested
  `<module>.<outer>.<inner>`. Node: `DialectStatement("python.function")` with
  `name`, `params` (RoutineParam incl. converted defaults), `decorators`,
  `calls` (converted ETL-relevant call nodes).

## References
- `calls` — calls to module-local defs and `import`/`from … import` names
  (absolute + relative). Stdlib and common third-party packages are skipped.
- `etl_reads` — `read_sql_table("t", schema="s")` → `s.t`; `read_sql("t"…)`
  when the argument is a bare identifier.
- `etl_writes` — `df.to_sql("t", schema="s")`.

## EmbeddedSql
`execute`/`executemany`/`executescript`, `read_sql*`/`pd.read_sql*` and
`sqlalchemy …execute(text(…))` first-arg SQL; `cursor.callproc("p")` becomes a
`calls` ref. Unresolvable SQL → info issue `python.dynamic_sql`.

## DialectExpr / DialectStatement names
`python.module`, `python.function`, `python.import`, `python.with`,
`python.with_item`, `python.try`, `python.except`, `python.class`,
`python.delete`, `python.expr_stmt`, `python.call`, `python.starred`,
`python.double_starred`, `python.fstring`, `python.formatted`,
`python.dict`, `python.slice`, plus `python.<lowercase node type>` for
unhandled AST nodes (comprehensions, await, …) — never raw source.

## Shared modules
`common.py` (EmbeddedSql, EtlParseResult, decode, qname, span, redact,
normalize_path, file_unparsed, EtlBuilder), `expr.py` (EtlExprParser +
parse_expression for ETL expression languages — used by the XML tools),
`recover.py` (SyntaxError block blanking, up to 20 rounds) and
`pyconvert.py` (CPython ast → MigraAI ast) are shared by shell/spark/airflow
(and handoff-2 tools).

## Routing
`parse` sniffs imports: `airflow` → `migraai.etl.airflow.parse`,
`pyspark`/`delta`/`SparkSession` → `migraai.etl.spark.parse`. Delegation never
recurses.

## Known gaps
- F-string placeholders in resolved SQL become `:name` params.
- `depth > 500` expressions → Unparsed + error.
