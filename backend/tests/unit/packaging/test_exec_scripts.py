"""Pure-output tests for migraai.packaging.scripts.build_execution_scripts."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

from migraai.packaging.model import PackagedFile
from migraai.packaging.sample import sample_package
from migraai.packaging.scripts import TARGETS, build_execution_scripts

RUNNERS_DIR = Path(__file__).resolve().parents[3] / "migraai" / "packaging" / "runners"

ALLOWED_DOC_HOSTS = {
    "docs.snowflake.com",
    "docs.databricks.com",
    "docs.cloud.google.com",
    "www.postgresql.org",
    "docs.aws.amazon.com",
    "learn.microsoft.com",
    "clickhouse.com",
    "airflow.apache.org",
}

FORBIDDEN = [
    re.compile(r"(?i)password\s*[=:]\s*\S"),
    re.compile(r"dapi[0-9a-f]{8}"),
    re.compile(r"cog_"),
    re.compile(r"apk_"),
    re.compile(r"/home/"),
    re.compile(r"/Users/"),
    re.compile(r"[A-Za-z]:\\\\"),
    re.compile(r"/tmp/"),
]


def spec_for(target: str) -> dict:
    return yaml.safe_load((RUNNERS_DIR / target / "target.yaml").read_text())


def materialize(root: Path, pkg, outputs: dict[str, bytes]) -> Path:
    """Write converted/ + execution/ trees mirroring the ZIP layout."""
    for obj in pkg.included():
        for f in obj.files:
            dst = root / "converted" / f.package_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(f.content, encoding="utf-8")
    for key, data in outputs.items():
        dst = root / "execution" / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    return root


def with_files(pkg, object_index: int, files) -> object:
    """Return a PackageInput with extra PackagedFiles on one included object."""
    objects = list(pkg.objects)
    obj = objects[object_index]
    objects[object_index] = dataclasses.replace(obj, files=obj.files + tuple(files))
    return dataclasses.replace(pkg, objects=tuple(objects))


@pytest.mark.parametrize("target", TARGETS)
def test_keys_and_order(target: str) -> None:
    pkg = sample_package(target)
    out = build_execution_scripts(pkg)
    assert set(out) == {
        f"{target}/order.txt",
        f"{target}/run.sh",
        f"{target}/run.ps1",
        f"{target}/README.md",
    }
    assert out == build_execution_scripts(pkg)  # deterministic

    order = out[f"{target}/order.txt"].decode()
    lines = [x for x in order.splitlines() if x and not x.startswith("#")]
    assert [x.split("\t")[1] for x in lines] == ["RUN", "REVIEW", "RUN"]
    assert [x.split("\t")[2] for x in lines] == [
        "teradata-testpack/tables-converted.sql",
        "teradata-testpack/views-converted.sql",
        "teradata-testpack/security-converted.sql",
    ]
    # The failed object appears nowhere except the README "Not included" list.
    for key, data in out.items():
        text = data.decode("utf-8-sig")
        assert "load_script" not in text or key.endswith("README.md")
    readme = out[f"{target}/README.md"].decode("utf-8-sig")
    assert "`load_script.sql` (failed)" in readme


@pytest.mark.parametrize("target", TARGETS)
def test_run_scripts_plan_matches_order(target: str) -> None:
    out = build_execution_scripts(sample_package(target))
    sh = out[f"{target}/run.sh"].decode()
    ps = out[f"{target}/run.ps1"].decode("utf-8-sig")  # may carry a BOM
    assert "'teradata-testpack/tables-converted.sql'" in sh
    assert "'teradata-testpack/views-converted.sql'" in sh
    ps1 = ps.replace("''", "'")  # PS-escaped quotes
    for p in (
        "teradata-testpack/tables-converted.sql",
        "teradata-testpack/views-converted.sql",
        "teradata-testpack/security-converted.sql",
    ):
        assert p in ps1
    assert "@@" not in sh and "@@" not in ps


def test_unknown_target() -> None:
    pkg = dataclasses.replace(sample_package("snowflake"), target_platform="oracle")
    with pytest.raises(ValueError, match="oracle"):
        build_execution_scripts(pkg)


def test_etl_files_in_readme_only() -> None:
    pkg = with_files(
        sample_package("databricks"),
        0,
        [
            PackagedFile(
                "teradata-testpack/job-converted.py",
                "python",
                "# Databricks notebook source\nx = 1\n",
                etl_format_id="databricks.notebook",
            ),
            PackagedFile(
                "teradata-testpack/tasks-converted.tasks.sql",
                "sql",
                "CREATE OR REPLACE TASK t AS SELECT 1;\n",
                etl_format_id="snowflake.tasks_sql",
            ),
        ],
    )
    out = build_execution_scripts(pkg)
    order = out["databricks/order.txt"].decode()
    run_lines = [x for x in order.splitlines() if not x.startswith("#")]
    assert all("job-converted" not in x and "tasks-converted" not in x for x in run_lines)
    assert "Not run automatically (ETL, see README.md): teradata-testpack/job-converted.py" in order

    readme = out["databricks/README.md"].decode()
    assert "databricks workspace import" in readme
    assert '"../../converted/teradata-testpack/job-converted.py"' in readme
    assert "docs.databricks.com/aws/en/dev-tools/cli/reference/workspace-commands" in readme
    # Other target's format: generic fallback citing FORMAT_SOURCES (create-task URL).
    assert "tasks-converted.tasks.sql" in readme
    assert "docs.snowflake.com/en/sql-reference/sql/create-task" in readme

    sh = out["databricks/run.sh"].decode()
    assert "job-converted" not in sh and "tasks-converted" not in sh


@pytest.mark.parametrize(
    "bad",
    ["../x.sql", "/abs.sql", "C:/x.sql", "a\\b.sql", "a//b.sql", "a/./b.sql", "ctl\x01.sql", ""],
    ids=["dotdot", "abs", "drive", "backslash", "emptyseg", "dotseg", "ctrl", "empty"],
)
def test_path_validation(bad: str) -> None:
    pkg = with_files(
        sample_package("snowflake"),
        0,
        [PackagedFile(bad or " ", "sql", "SELECT 1;\n")],
    )
    if bad == "":
        pkg = with_files(sample_package("snowflake"), 0, [PackagedFile("", "sql", "SELECT 1;\n")])
    with pytest.raises(ValueError):
        build_execution_scripts(pkg)


@pytest.mark.parametrize("target", TARGETS)
def test_no_credentials_or_absolute_paths(target: str) -> None:
    out = build_execution_scripts(sample_package(target))
    for key, data in out.items():
        text = data.decode("utf-8-sig")
        for rx in FORBIDDEN:
            assert not rx.search(text), f"{key}: matched {rx.pattern}"
        for m in re.finditer(r"https?://[^\s\)'\"`>]+", text):
            host = urlparse(m.group(0)).hostname or ""
            assert host in ALLOWED_DOC_HOSTS, f"{key}: unexpected host {host}"


def nonascii_pkg(target: str):
    """Sample package plus a runnable file with a non-ASCII package_path."""
    return with_files(
        sample_package(target),
        0,
        [PackagedFile("teradata-testpack/tâbles-converted.sql", "sql", "SELECT 1;\n")],
    )


@pytest.mark.parametrize("target", TARGETS)
def test_nonascii_path_bom(target: str) -> None:
    """Only run.ps1 may carry a BOM; run.sh must keep its #! first line."""
    out = build_execution_scripts(nonascii_pkg(target))
    assert out[f"{target}/run.sh"].startswith(b"#!")
    assert out[f"{target}/run.ps1"].startswith(b"\xef\xbb\xbf")
    assert not out[f"{target}/order.txt"].startswith(b"\xef\xbb\xbf")
    assert not out[f"{target}/README.md"].startswith(b"\xef\xbb\xbf")
    assert "tâbles-converted.sql" in out[f"{target}/run.sh"].decode()


@pytest.mark.parametrize("target", TARGETS)
def test_masked_listed(target: str) -> None:
    pkg = sample_package(target)
    objects = list(pkg.objects)
    grant = objects[2]
    f = grant.files[0]
    objects[2] = dataclasses.replace(
        grant,
        files=(dataclasses.replace(f, content=f.content.replace("REPORTING", "***MASKED***")),),
    )
    pkg = dataclasses.replace(pkg, objects=tuple(objects))
    out = build_execution_scripts(pkg)
    order = out[f"{target}/order.txt"].decode()
    assert "# MASKED secrets in: teradata-testpack/security-converted.sql" in order
    readme = out[f"{target}/README.md"].decode()
    assert "Masked secrets" in readme and "security-converted.sql" in readme
