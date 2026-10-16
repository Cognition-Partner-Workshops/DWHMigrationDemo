"""Runtime tests: execute generated run.sh / run.ps1 against a fake CLI."""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_exec_scripts import materialize, nonascii_pkg, spec_for, with_files

from migraai.packaging.model import PackagedFile
from migraai.packaging.sample import sample_package
from migraai.packaging.scripts import TARGETS, build_execution_scripts

TIMEOUT = 60

FAKE_SH = """#!/bin/sh
c=$(cat "$FAKE_COUNT" 2>/dev/null || echo 0)
c=$((c + 1))
echo "$c" > "$FAKE_COUNT"
printf '%s' "$(basename "$0")" >> "$FAKE_LOG"
for a in "$@"; do printf '\\037%s' "$a" >> "$FAKE_LOG"; done
printf '\\n' >> "$FAKE_LOG"
if [ "$FAKE_READ_STDIN" = "1" ]; then
    head -n 1 > "$FAKE_STDIN_LOG" 2>/dev/null || true
fi
if [ -n "$FAKE_FAIL_AT" ] && [ "$c" -ge "$FAKE_FAIL_AT" ]; then exit 7; fi
exit 0
"""

# On Windows an extensionless shell script can't be exec'd; the fake CLI is a
# .cmd wrapper around this Python helper (python is on PATH in CI).
FAKE_PY = """import os
import sys

prog = sys.argv[1]
args = sys.argv[2:]
count_path = os.environ["FAKE_COUNT"]
try:
    c = int(open(count_path).read().strip() or "0")
except OSError:
    c = 0
c += 1
open(count_path, "w").write(str(c))
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(prog)
    for a in args:
        fh.write("\\x1f" + a)
    fh.write("\\n")
if os.environ.get("FAKE_READ_STDIN") == "1":
    first = sys.stdin.buffer.readline()
    open(os.environ["FAKE_STDIN_LOG"], "wb").write(first)
fail = os.environ.get("FAKE_FAIL_AT")
if fail and c >= int(fail):
    sys.exit(7)
"""

FAKE_CMD = """@echo off
python "%~dp0fake_cli.py" %~n0 %*
"""

BASH = shutil.which("bash")
NEEDS_BASH = pytest.mark.skipif(
    BASH is None or sys.platform == "win32", reason="bash unavailable or WSL stub on Windows"
)
PWSH = shutil.which("pwsh")
POWERSHELL = shutil.which("powershell")
PS_SHELLS = [s for s in (PWSH, POWERSHELL) if s]
PS_SHELL_PARAMS = [pytest.param(s, id=Path(s).name) for s in PS_SHELLS] or [
    pytest.param("none", id="none")
]
NEEDS_PS = pytest.mark.skipif(not PS_SHELLS, reason="no PowerShell interpreter found")


def run_dir(tmp_path: Path, target: str, pkg=None) -> tuple[Path, Path, object]:
    pkg = pkg or sample_package(target)
    out = build_execution_scripts(pkg)
    root = materialize(tmp_path / "pkg", pkg, out)
    return root / "execution" / target, root, pkg


def fake_bin(tmp_path: Path, cli: str) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    if sys.platform == "win32":
        (bin_dir / "fake_cli.py").write_text(FAKE_PY)
        (bin_dir / f"{cli}.cmd").write_text(FAKE_CMD)
    else:
        fake = bin_dir / cli
        fake.write_text(FAKE_SH)
        fake.chmod(0o755)
    return bin_dir


def fake_env(tmp_path: Path, bin_dir: Path, stdin: bool, fail_at: int | None = None) -> dict:
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    env["FAKE_LOG"] = str(tmp_path / "fake.log")
    env["FAKE_COUNT"] = str(tmp_path / "fake.count")
    env["FAKE_STDIN_LOG"] = str(tmp_path / "fake.stdin")
    env["FAKE_READ_STDIN"] = "1" if stdin else "0"
    if fail_at is not None:
        env["FAKE_FAIL_AT"] = str(fail_at)
    else:
        env.pop("FAKE_FAIL_AT", None)
    return env


def calls(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "fake.log"
    if not log.exists():
        return []
    return [line.split("\x1f") for line in log.read_text().splitlines() if line]


def masked_pkg(target: str):
    pkg = sample_package(target)
    objects = list(pkg.objects)
    grant = objects[2]
    f = grant.files[0]
    objects[2] = dataclasses.replace(
        grant,
        files=(dataclasses.replace(f, content=f.content.replace("REPORTING", "***MASKED***")),),
    )
    return dataclasses.replace(pkg, objects=tuple(objects))


@pytest.mark.parametrize("target", TARGETS)
@NEEDS_BASH
def test_bash_syntax(tmp_path: Path, target: str) -> None:
    exe_dir, _, _ = run_dir(tmp_path, target)
    r = subprocess.run(
        [BASH, "-n", "run.sh"], cwd=exe_dir, capture_output=True, text=True, timeout=TIMEOUT
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("target", TARGETS)
@NEEDS_BASH
def test_bash_dry_run(tmp_path: Path, target: str) -> None:
    exe_dir, _, _ = run_dir(tmp_path, target)
    spec = spec_for(target)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), spec.get("stdin", False))
    r = subprocess.run(
        [BASH, "run.sh"], cwd=exe_dir, env=env, capture_output=True, text=True, timeout=TIMEOUT
    )
    assert r.returncode == 0, r.stderr
    assert calls(tmp_path) == []
    assert "[001] RUN    teradata-testpack/tables-converted.sql" in r.stdout
    assert "SKIP   teradata-testpack/views-converted.sql" in r.stdout
    assert "Dry run only" in r.stdout


@pytest.mark.parametrize("target", TARGETS)
@NEEDS_BASH
def test_bash_execute_order(tmp_path: Path, target: str) -> None:
    spec = spec_for(target)
    exe_dir, _, _ = run_dir(tmp_path, target)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), spec.get("stdin", False))
    r = subprocess.run(
        [BASH, "run.sh", "--execute"],
        cwd=exe_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert r.returncode == 0, r.stderr
    log = calls(tmp_path)
    assert len(log) == 2  # review file skipped
    if spec.get("stdin"):
        stdin_log = (tmp_path / "fake.stdin").read_text()
        assert stdin_log.startswith("-- Converted")
        assert "--use_legacy_sql=false" in log[-1]
        assert not any(a.endswith(".sql") for a in log[-1])
        return
    assert log[0][-1].endswith("converted/teradata-testpack/tables-converted.sql")
    assert log[1][-1].endswith("converted/teradata-testpack/security-converted.sql")


@pytest.mark.parametrize("target", TARGETS)
@NEEDS_BASH
def test_bash_execute_include_review(tmp_path: Path, target: str) -> None:
    spec = spec_for(target)
    exe_dir, _, _ = run_dir(tmp_path, target)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), spec.get("stdin", False))
    r = subprocess.run(
        [BASH, "run.sh", "--execute", "--include-review"],
        cwd=exe_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert r.returncode == 0, r.stderr
    assert len(calls(tmp_path)) == 3
    if not spec.get("stdin"):
        assert calls(tmp_path)[1][-1].endswith("converted/teradata-testpack/views-converted.sql")


@pytest.mark.parametrize("target", TARGETS)
@NEEDS_BASH
def test_bash_failure_stops(tmp_path: Path, target: str) -> None:
    spec = spec_for(target)
    exe_dir, _, _ = run_dir(tmp_path, target)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), spec.get("stdin", False), fail_at=2)
    r = subprocess.run(
        [BASH, "run.sh", "--execute", "--include-review"],
        cwd=exe_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert r.returncode == 7
    assert "FAILED:" in r.stderr
    assert "views-converted.sql" in r.stderr
    assert len(calls(tmp_path)) == 2


@pytest.mark.parametrize("target", TARGETS)
@NEEDS_BASH
def test_bash_masked_refuses_execute(tmp_path: Path, target: str) -> None:
    spec = spec_for(target)
    pkg = masked_pkg(target)
    exe_dir, _, _ = run_dir(tmp_path, target, pkg)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), spec.get("stdin", False))
    r = subprocess.run(
        [BASH, "run.sh", "--execute"],
        cwd=exe_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert r.returncode == 3
    assert calls(tmp_path) == []
    assert "replace the masked secret" in r.stderr
    assert "security-converted.sql" in r.stderr
    r2 = subprocess.run(
        [BASH, "run.sh"], cwd=exe_dir, env=env, capture_output=True, text=True, timeout=TIMEOUT
    )
    assert r2.returncode == 0
    assert "WARNING" in r2.stdout


@pytest.mark.parametrize("target", TARGETS)
@NEEDS_BASH
def test_bash_missing_cli(tmp_path: Path, target: str) -> None:
    exe_dir, _, _ = run_dir(tmp_path, target)
    # Minimal PATH: only the tools run.sh needs, so the target CLI can't be found
    # even if it is installed on this machine.
    minimal = tmp_path / "minbin"
    minimal.mkdir()
    for tool in ("dirname", "grep", "cat", "head", "basename"):
        src = shutil.which(tool)
        if src:
            (minimal / tool).symlink_to(src)
    env = fake_env(tmp_path, tmp_path, False)
    env["PATH"] = str(minimal)
    r = subprocess.run(
        [BASH, "run.sh", "--execute"],
        cwd=exe_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert r.returncode == 127


@NEEDS_BASH
def test_bash_nonascii_path(tmp_path: Path) -> None:
    target = "snowflake"
    pkg = nonascii_pkg(target)
    spec = spec_for(target)
    exe_dir, _, _ = run_dir(tmp_path, target, pkg)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), False)
    r = subprocess.run(
        [BASH, "run.sh", "--execute"],
        cwd=exe_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert r.returncode == 0, r.stderr
    assert calls(tmp_path)[1][-1].endswith("converted/teradata-testpack/tâbles-converted.sql")


@NEEDS_BASH
def test_bash_space_and_quote_path(tmp_path: Path) -> None:
    target = "snowflake"
    pkg = with_files(
        sample_package(target),
        0,
        [PackagedFile("my pack/it's tables-converted.sql", "sql", "SELECT 1;\n")],
    )
    spec = spec_for(target)
    exe_dir, _, _ = run_dir(tmp_path, target, pkg)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), False)
    r = subprocess.run(
        [BASH, "run.sh", "--execute"],
        cwd=exe_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert r.returncode == 0, r.stderr
    log = calls(tmp_path)
    assert log[1][-1].endswith("converted/my pack/it's tables-converted.sql")


@NEEDS_BASH
def test_bash_passthrough_not_echoed(tmp_path: Path) -> None:
    target = "snowflake"
    spec = spec_for(target)
    exe_dir, _, _ = run_dir(tmp_path, target)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), False)
    r = subprocess.run(
        [BASH, "run.sh", "--execute", "--", "-c", "myconn"],
        cwd=exe_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    assert r.returncode == 0, r.stderr
    assert "myconn" not in r.stdout and "myconn" not in r.stderr
    assert "(+2 extra CLI args)" in r.stdout
    first = calls(tmp_path)[0]
    assert "-c" in first and "myconn" in first


@NEEDS_BASH
def test_bash_unknown_arg(tmp_path: Path) -> None:
    exe_dir, _, _ = run_dir(tmp_path, "snowflake")
    r = subprocess.run(
        [BASH, "run.sh", "--bogus"], cwd=exe_dir, capture_output=True, text=True, timeout=TIMEOUT
    )
    assert r.returncode == 2


# ---------------------------------------------------------------- PowerShell --

PARSE_PS1 = """
$t = $null; $e = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    $env:PARSE_FILE, [ref]$t, [ref]$e)
foreach ($err in $e) {
    [Console]::Error.WriteLine("$($err.Extent.StartLineNumber): $($err.Message)")
}
exit $e.Count
"""


def ps_run(shell: str, args: list[str], cwd: Path, env: dict | None = None, timeout=TIMEOUT):
    base = [shell, "-NoProfile", "-NonInteractive"]
    if Path(shell).name == "powershell":
        base += ["-ExecutionPolicy", "Bypass"]
    return subprocess.run(
        base + args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
    )


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("shell", PS_SHELL_PARAMS)
@NEEDS_PS
def test_ps_parse(tmp_path: Path, target: str, shell: str) -> None:
    exe_dir, _, _ = run_dir(tmp_path, target)
    parser = tmp_path / "parse.ps1"
    parser.write_text(PARSE_PS1)
    env = dict(os.environ, PARSE_FILE=str(exe_dir / "run.ps1"))
    r = ps_run(shell, ["-File", str(parser)], exe_dir, env)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("shell", PS_SHELL_PARAMS)
@NEEDS_PS
def test_ps_dry_run_and_execute(tmp_path: Path, target: str, shell: str) -> None:
    spec = spec_for(target)
    exe_dir, _, _ = run_dir(tmp_path, target)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), spec.get("stdin", False))
    r = ps_run(shell, ["-File", "run.ps1"], exe_dir, env)
    assert r.returncode == 0, r.stderr
    assert calls(tmp_path) == []
    assert "[001] RUN    teradata-testpack/tables-converted.sql" in r.stdout
    assert "SKIP" in r.stdout and "Dry run only" in r.stdout

    r = ps_run(shell, ["-File", "run.ps1", "-Execute", "-IncludeReview"], exe_dir, env)
    assert r.returncode == 0, r.stderr
    log = calls(tmp_path)
    assert len(log) == 3
    if spec.get("stdin"):
        # PS 5.1 pipes a BOM to native stdin; pwsh does not.
        assert (tmp_path / "fake.stdin").read_text().lstrip("\ufeff").startswith("-- Converted")
    else:
        assert log[0][-1].endswith(
            os.path.join("converted", "teradata-testpack", "tables-converted.sql")
        )
        assert log[2][-1].endswith("security-converted.sql")


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("shell", PS_SHELL_PARAMS)
@NEEDS_PS
def test_ps_failure_and_masked(tmp_path: Path, target: str, shell: str) -> None:
    spec = spec_for(target)
    exe_dir, _, _ = run_dir(tmp_path, target)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), spec.get("stdin", False), fail_at=2)
    r = ps_run(shell, ["-File", "run.ps1", "-Execute", "-IncludeReview"], exe_dir, env)
    assert r.returncode == 7, r.stderr
    assert "FAILED:" in r.stderr
    assert len(calls(tmp_path)) == 2

    pkg = masked_pkg(target)
    exe_dir2, _, _ = run_dir(tmp_path / "m", target, pkg)
    env2 = fake_env(tmp_path, tmp_path / "fakebin", spec.get("stdin", False))
    env2["FAKE_LOG"] = str(tmp_path / "fake2.log")
    r = ps_run(shell, ["-File", "run.ps1", "-Execute"], exe_dir2, env2)
    assert r.returncode == 3
    assert "replace the masked secret" in r.stderr


@pytest.mark.parametrize("shell", PS_SHELL_PARAMS)
@NEEDS_PS
def test_ps_space_and_quote_path(tmp_path: Path, shell: str) -> None:
    target = "snowflake"
    pkg = with_files(
        sample_package(target),
        0,
        [PackagedFile("my pack/it's tables-converted.sql", "sql", "SELECT 1;\n")],
    )
    spec = spec_for(target)
    exe_dir, _, _ = run_dir(tmp_path, target, pkg)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), False)
    r = ps_run(shell, ["-File", "run.ps1", "-Execute"], exe_dir, env)
    assert r.returncode == 0, r.stderr
    assert calls(tmp_path)[1][-1].endswith(
        os.path.join("converted", "my pack", "it's tables-converted.sql")
    )


@pytest.mark.parametrize("shell", PS_SHELL_PARAMS)
@NEEDS_PS
def test_ps_passthrough_command_mode(tmp_path: Path, shell: str) -> None:
    """-Command invocation binds -CliArgs as an array (unlike -File)."""
    target = "snowflake"
    spec = spec_for(target)
    exe_dir, _, _ = run_dir(tmp_path, target)
    env = fake_env(tmp_path, fake_bin(tmp_path, spec["cli"]), False)
    ps_path = str(exe_dir / "run.ps1").replace("'", "''")
    command = f"& '{ps_path}' -Execute -CliArgs '-c','myconn'"
    r = ps_run(shell, ["-Command", command], exe_dir, env)
    assert r.returncode == 0, r.stderr
    assert "myconn" not in r.stdout and "myconn" not in r.stderr
    first = calls(tmp_path)[0]
    # 'snow sql -c myconn --enable-templating NONE -f <file>'
    assert first[1:4] == ["sql", "-c", "myconn"]
