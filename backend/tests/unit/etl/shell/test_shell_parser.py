from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from migraai.discovery import ast
from migraai.etl.shell.lexer import tokenize
from migraai.etl.shell.parse import parse, parse_command_line
from migraai.etl.shell.parser import parse_script

GOLDEN_DIR = Path(__file__).parent / "golden"


def _run(text: str):
    return parse(text.encode(), "bin/job.sh")


def test_simple_command_and_assignments() -> None:
    statements, variables, _ = parse_script("A=1\nexport B=x\necho hi $A\n")
    assert variables == {"A": "1", "B": "x"}
    cmd = statements[-1]
    assert cmd.name == "shell.command"
    assert cmd.args["program"].value == "echo"


def test_pipeline_and_or() -> None:
    statements, _, _ = parse_script("a | b | c && d || e\n")
    node = statements[0]
    assert node.name == "shell.and_or"
    assert node.args["op"].value == "||"
    left = node.args["left"]
    assert left.name == "shell.and_or" and left.args["op"].value == "&&"
    assert left.args["left"].name == "shell.pipeline"
    assert len(left.args["left"].args["commands"]) == 3


def test_if_for_while_case() -> None:
    text = """if test -f cfg; then
  echo yes
elif test -f cfg2; then
  echo mid
else
  echo no
fi
for f in a b c; do
  echo $f
done
i=0
while test $i -lt 3; do
  i=$((i+1))
done
until test $i -gt 5; do
  i=$((i+1))
done
case $1 in
  start) echo go ;;
  stop|halt) echo bye ;;
  *) echo other ;;
esac
"""
    statements, _, issues = parse_script(text)
    kinds = [s.name if isinstance(s, ast.DialectStatement) else s.kind for s in statements]
    assert "if" in kinds and "for_loop" in kinds
    whiles = [s for s in statements if isinstance(s, ast.While)]
    assert len(whiles) == 2 and isinstance(whiles[1].condition, ast.Unary)
    case = next(s for s in statements if getattr(s, "name", "") == "shell.case")
    assert len(case.args["arms"]) == 3
    assert not [i for i in issues if i.severity == "error"]


def test_function_defs_all_forms() -> None:
    text = """f() {
  echo in_f
}
function g {
  echo in_g
}
function h() {
  echo in_h
}
f
g
"""
    statements, _, _ = parse_script(text)
    fns = [s for s in statements if getattr(s, "name", "") == "shell.function"]
    assert len(fns) == 3


def test_group_and_subshell() -> None:
    statements, _, _ = parse_script("( echo a; echo b )\n{ echo c; }\n")
    assert statements[0].name == "shell.group"
    assert statements[0].args["subshell"].value == "true"
    assert statements[1].name == "shell.group"
    assert statements[1].args["subshell"].value == "false"


def test_word_expansions() -> None:
    tokens, _ = tokenize("echo a${V}x $W ${D:-def} $(sub cmd) `old` $((1+2))\n")
    word = tokens[1]
    kinds = [p[0] for p in word.parts]
    assert kinds[0] == "lit"
    assert "var" in "".join(kinds) or "param" in kinds
    tokens2, _ = tokenize("echo $W\n")
    assert tokens2[1].parts == [("var", "W")]


def test_heredoc_attached_to_redirect() -> None:
    statements, _, issues = parse_script("cat <<'EOF'\nline one\nline two\nEOF\n")
    cmd = statements[0]
    redirect = cmd.args["redirects"][0]
    assert redirect.name == "shell.redirect"
    assert "line one" in redirect.args["heredoc"].value
    assert not [i for i in issues if i.severity == "error"]


def test_heredoc_tab_strip() -> None:
    statements, _, _ = parse_script("cat <<-EOF\n\tindented\nEOF\n")
    body = statements[0].args["redirects"][0].args["heredoc"].value
    assert body == "indented"


def test_command_substitution_nested_parse() -> None:
    statements, _, _ = parse_script("echo $(inner one | two)\n")
    arg = statements[0].args["args"][0]
    assert arg.name == "shell.command_substitution"
    assert arg.args["statements"][0].name == "shell.pipeline"


def test_unsupported_constructs() -> None:
    statements, _, issues = parse_script('eval "$CMD"\nselect x in a; do echo; done\n')
    names = [s.name for s in statements]
    assert "shell.unsupported" in names
    assert any(i.code == "shell.unsupported_construct" for i in issues)


def test_recovery_unterminated_quote() -> None:
    statements, _, issues = parse_script("echo 'oops\necho fine\n")
    assert any(i.code == "shell.syntax" and i.severity == "error" for i in issues)
    assert statements[-1].args["program"].value == "echo"
    assert any(isinstance(arg, ast.Unparsed) for arg in statements[0].args["args"]) or isinstance(
        statements[0].args["args"][-1], ast.Unparsed
    )


def test_recovery_missing_fi() -> None:
    statements, _, issues = parse_script("if true; then echo x\n")
    assert isinstance(statements[0], ast.Unparsed)
    assert any("fi" in i.message for i in issues)


def test_objects_and_function_calls() -> None:
    result = _run("helper() {\n  echo x\n}\nhelper\n")
    by = {o.qualified_name: o for o in result.objects}
    assert by["bin/job.sh::helper"].type == "function"
    assert ("calls", "bin/job.sh::helper") in names(by["bin/job.sh"])


def names(obj):
    return [(r.ref_kind, ".".join(p.value for p in r.name.parts)) for r in obj.references]


def test_parse_command_line_api() -> None:
    script = parse_command_line("sqlplus u/p@d @x.sql && sh y.sh", "dags/t.py", 5)
    from migraai.etl.shell.parse import extract_invocations

    invocations = extract_invocations(script.statements, "dags/t.py", script.variables)
    assert any(i.ref_kind == "etl_runs" and "x.sql" in (i.name or "") for i in invocations)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00\xff\xfe garbage \x80",
        b"((((((((",
        b"echo 'unterminated",
        b"if if if if fi fi",
        b"a" * 100000,
    ],
)
def test_never_raises_on_garbage(data: bytes) -> None:
    result = parse(data, "g/x.sh")
    assert result.objects or result.issues is not None


def test_nothing_executed() -> None:
    result = _run("rm -rf /tmp/never\nexit 9\n")
    assert result.objects[0].type == "script"


def test_ast_golden() -> None:
    result = _run("if test -f x; then\n  bteq <<SQL\nSELECT 1;\nSQL\nfi\n")
    script = next(o for o in result.objects if o.type == "script")
    actual = script.node.to_dict()
    golden = GOLDEN_DIR / "script_if_bteq.json"
    if os.environ.get("MIGRAAI_REGEN_GOLDEN") == "1" or not golden.exists():
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert json.loads(golden.read_text()) == actual


def test_invocations_matrix() -> None:
    text = """bteq <<'EOT'
.LOGON td/u,pw
SELECT 1 FROM t;
EOT
sqlplus u/p@d @s.sql
bcp sales.t in data.dat -S srv -U u -P pw
bcp sales.t2 out data2.dat
nzsql -w pw -c 'SELECT 1'
nzload -t dwh.t -df /data/f.dat
db2 "SELECT 1 FROM x"
db2 -tvf script.sql
beeline -e 'SELECT 1'
hive -f q.hql
snowsql -q 'SELECT 1'
spark-submit --class com.a.M --master yarn app.py
sh sub/step.sh
./local.sh
source lib/vars.sh
python3 etl/run.py
"""
    result = _run(text)
    script = next(o for o in result.objects if o.type == "script")
    refs = names(script)
    assert ("etl_writes", "sales.t") in refs
    assert ("etl_reads", "sales.t2") in refs
    assert ("etl_writes", "dwh.t") in refs
    assert ("etl_runs", "com.a.M") in refs
    assert ("etl_runs", "bin/app.py") in refs
    assert ("etl_runs", "sub/step.sh") in refs
    assert ("etl_runs", "lib/vars.sh") in refs
    assert ("etl_runs", "etl/run.py") in refs
    sqls = {(e.dialect_hint, e.text.strip()[:6]) for e in result.embedded_sql}
    assert ("netezza", "SELECT") in sqls
    assert ("db2", "SELECT") in sqls
    assert ("hive", "SELECT") in sqls
    assert ("snowflake", "SELECT") in sqls
    assert any(e.dialect_hint == "teradata" for e in result.embedded_sql)


def test_variable_substitution_in_paths() -> None:
    result = _run("DIR=jobs\nsh $DIR/run.sh\n")
    script = result.objects[0]
    assert ("etl_runs", "jobs/run.sh") in names(script)


def test_secrets_never_appear() -> None:
    text = """sqlplus etl/s3cretpw@prod @x.sql
bcp t in f -P hunter2
"""
    result = _run(text)
    payload = json.dumps([o.to_dict() for o in result.objects]) + json.dumps(
        [e.text for e in result.embedded_sql]
    )
    assert "s3cretpw" not in payload and "hunter2" not in payload


def test_pipeline_local_refs_resolve_internal() -> None:
    from migraai.discovery.pipeline import discover_files

    files = {
        "jobs/run_all.sh": (
            b"main() {\n  bash jobs/stage.sh\n  finish_up\n}\nfinish_up() {\n  echo done\n}\nmain\n"
        ),
        "jobs/stage.sh": b"stage() {\n  echo staging\n}\nstage\n",
    }
    objects, edges = discover_files(files, "ansi")
    local = {o.qualified_name.casefold() for o in objects if o.status != "external"}
    edge_map = {(e.source, e.target, e.kind): e for e in edges}
    calls = edge_map.get(("jobs/run_all.sh::main", "jobs/run_all.sh::finish_up", "calls"))
    assert calls is not None and calls.external is False
    runs = edge_map.get(("jobs/run_all.sh::main", "jobs/stage.sh", "etl_runs"))
    assert runs is not None and runs.external is False
    dupes = [
        o.qualified_name
        for o in objects
        if o.status == "external" and o.qualified_name.casefold() in local
    ]
    assert not dupes
