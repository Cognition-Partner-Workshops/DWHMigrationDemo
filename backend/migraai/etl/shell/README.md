# shell ETL parser

`parse(data, rel_path) -> EtlParseResult` for `.sh`/`.bash` files.

## Objects
- `script` — qualified by rel_path. Node: `DialectStatement("shell.script")`.
- `function` — `<rel_path>::<name>` for `f() {…}`, `function f {…}`. Refs/SQL
  owner = innermost enclosing function, else the script. Calling a function
  defined in the same file emits a `calls` ref.

## Nodes
`shell.command` (program/args/redirects/assignments), `shell.pipeline`,
`shell.and_or`, `shell.case` + `shell.case_arm`, `shell.group` (subshell flag),
`shell.function`, `shell.unsupported` (eval/coproc/select/process-substitution
→ warning `shell.unsupported_construct`). Words: Literal / Variable /
`shell.word` parts (`shell.param_expansion`, `shell.command_substitution`,
`shell.arithmetic`). Redirects: `shell.redirect` (op, target, heredoc).
`if`/`for`/`while`/`until` map to ast.If/ForLoop/While (until wraps `not`).

## Invocations (refs + EmbeddedSql)
bteq (teradata), sqlplus (oracle), bcp (sqlserver), nzsql/nzload (netezza),
db2, beeline/hive (hive), snowsql (snowflake); `spark-submit` (`--class` →
etl_runs dotted; primary resource → etl_runs path); runners `sh|bash|ksh`,
`./x.sh`, `. x`/`source x`, `python|python3 x`, `perl x` → etl_runs path
(normalized per §6). `-e/-c/-q`/`"sql"` strings and heredoc bodies → EmbeddedSql.

## Shared API
`parse_command_line(text, rel_path, line=1, variables=None) -> ShellScript`
and `extract_invocations(script_stmts, rel_path, variables, functions)` —
reused by airflow (`bash_command`) and handoff-2 tools (Control-M command
lines etc.).

## Recovery
Unterminated quote/heredoc/compound → `Unparsed` + error `shell.syntax`;
parsing resumes at the next line. Garbage never raises.

## Known gaps
- Case patterns kept as words (no glob AST).
- Arithmetic `$((…))` parsed as a flat left-assoc Binary over `+ - * / %`.
