from __future__ import annotations

from dataclasses import replace

import pytest

from migraai.packaging.model import PackagedFile
from migraai.packaging.naming import (
    converted_files,
    converted_path,
    default_package_name,
    package_name,
    upload_root,
)
from migraai.packaging.sample import sample_package


def test_converted_path_strips_archive_segments() -> None:
    assert (
        converted_path("teradata-testpack.zip/tables.sql", ".sql", set())
        == "teradata-testpack/tables-converted.sql"
    )
    assert converted_path("input/job.dtsx", ".py", set()) == "input/job-converted.py"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("teradata-testpack.zip/tables.sql", "teradata-testpack.zip"),
        ("A.ZIP/x/y.sql", "A.ZIP"),
        ("a.zip/inner.zip/x.sql", "a.zip"),
        ("dir/b.sql", "dir/b.sql"),
    ],
)
def test_upload_root_groups_archive_members(path: str, expected: str) -> None:
    assert upload_root(path) == expected


def test_converted_path_resolves_collisions() -> None:
    taken: set[str] = set()
    assert converted_path("tables.sql", ".sql", taken) == "tables-converted.sql"
    assert converted_path("tables.sql", ".sql", taken) == "tables-converted-2.sql"
    assert converted_path("tables.sql", ".sql", taken) == "tables-converted-3.sql"


@pytest.mark.parametrize(
    "path",
    ["../tables.sql", "folder/../tables.sql", "/absolute/tables.sql", "C:\\tables.sql"],
)
def test_converted_path_rejects_absolute_and_traversal_paths(path: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        converted_path(path, ".sql", set())


def test_package_names_are_sanitized_and_capped_at_80_characters() -> None:
    result = package_name("  customer exports / 2026!" + "x" * 90)
    assert result == "customer-exports-2026-" + "x" * 58
    assert len(result) == 80
    assert (
        default_package_name(["incoming/teradata-testpack.zip"], "teradata", "snowflake")
        == "teradata-testpack"
    )
    assert default_package_name([], "teradata", "snowflake") == "migraai-teradata-to-snowflake"


def test_objects_from_a_source_file_are_line_ordered_and_review_is_marked() -> None:
    package = sample_package()
    table, view = package.objects[:2]
    table = replace(
        table,
        source_line_start=20,
        files=(PackagedFile("source/procedure-converted.sql", "sql", "SELECT 20;"),),
    )
    view = replace(
        view,
        source_line_start=3,
        files=(PackagedFile("source/procedure-converted.sql", "sql", "SELECT 3;"),),
    )
    package = replace(package, objects=(table, view))

    contents = converted_files(package)["source/procedure-converted.sql"].decode("utf-8")
    assert contents.index("SELECT 3;") < contents.index("SELECT 20;")
    assert "-- NEEDS-REVIEW:" in contents
    assert contents.count("\n\n") == 1
