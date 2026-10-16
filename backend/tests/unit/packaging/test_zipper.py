from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import replace
from io import BytesIO

import pytest

from migraai.packaging.model import PackagedFile
from migraai.packaging.sample import sample_package
from migraai.packaging.zipper import build_zip


def test_zip_layout_manifest_review_headers_hashes_and_determinism() -> None:
    package = sample_package()
    json_review = replace(
        package.objects[1],
        object_id="obj-json",
        source_qualified_name="sales.json_review",
        target_qualified_name="SALES.JSON_REVIEW",
        object_type="view",
        source_file="teradata-testpack.zip/views.json",
        files=(
            PackagedFile(
                "teradata-testpack/views-review-converted.json", "json", '{"review":true}'
            ),
        ),
    )
    package = replace(package, objects=(*package.objects, json_review))
    parts = {
        "mapping/mapping-workbook.xlsx": b"workbook",
        "mapping/csv/objects.csv": b"id,name\nobj-1,customer_accounts\n",
        "execution/snowflake/run.sql": b"-- dry run\n",
        "report/session-report.md": b"# Report\n",
        "report/session-report.pdf": b"%PDF-test",
    }

    first = build_zip(package, parts)
    second = build_zip(package, parts)
    assert first == second

    with zipfile.ZipFile(BytesIO(first)) as archive:
        names = archive.namelist()
        root = "teradata-testpack-converted/"
        assert names == sorted(names)
        assert f"{root}manifest.json" in names
        assert f"{root}converted/" in names
        assert f"{root}mapping/" in names
        assert f"{root}mapping/csv/" in names
        assert f"{root}execution/snowflake/" in names
        assert f"{root}report/" in names
        assert f"{root}converted/teradata-testpack/tables-converted.sql" in names
        assert f"{root}mapping/mapping-workbook.xlsx" in names
        assert f"{root}mapping/csv/objects.csv" in names
        assert f"{root}execution/snowflake/run.sql" in names
        assert f"{root}report/session-report.md" in names
        assert f"{root}report/session-report.pdf" in names
        assert all("\\" not in name and ".." not in name for name in names)
        assert archive.getinfo(f"{root}manifest.json").date_time == (
            2026,
            10,
            16,
            0,
            0,
            0,
        )

        converted = {
            name.removeprefix(root): archive.read(name)
            for name in names
            if name.startswith(f"{root}converted/")
        }
        sql_review = converted["converted/teradata-testpack/views-converted.sql"].decode()
        assert "-- NEEDS-REVIEW:" in sql_review
        assert "CREATE VIEW SALES.V_ACTIVE" in sql_review
        assert not any("load_script.sql" in name for name in names)

        manifest = json.loads(archive.read(f"{root}manifest.json"))
        assert manifest["counts"]["included"] == 4
        assert manifest["counts"]["excluded"] == 1
        assert manifest["counts"]["external"] == 1
        assert manifest["egress"]["total_acus"] == "4.40"
        assert any(
            item["id"] == "obj-4" and item["status"] == "failed" for item in manifest["excluded"]
        )
        assert any(item["id"] == "obj-json" for item in manifest["needs_review"])
        assert manifest["needs_review"][-1]["json_needs_review_flag"] is True
        assert (
            "NEEDS-REVIEW"
            not in converted["converted/teradata-testpack/views-review-converted.json"].decode()
        )

        payloads = {
            name.removeprefix(root): archive.read(name)
            for name in names
            if name != f"{root}manifest.json" and not name.endswith("/")
        }
        assert set(manifest["sha256"]) == set(payloads)
        for path, digest in manifest["sha256"].items():
            assert hashlib.sha256(payloads[path]).hexdigest() == digest

        for data in payloads.values():
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            assert "***" not in text.replace("***MASKED***", "")
        manifest_text = archive.read(f"{root}manifest.json").decode("utf-8")
        assert "***" not in manifest_text.replace("***MASKED***", "")


@pytest.mark.parametrize(
    ("missing_indexes", "expected"),
    [
        ((0,), "3.20 (partial)"),
        ((0, 1, 2, 3), "not reported by the Devin API"),
    ],
)
def test_manifest_reports_partial_or_unavailable_acus(missing_indexes, expected) -> None:
    package = sample_package()
    sessions = tuple(
        replace(session, acus=None) if index in missing_indexes else session
        for index, session in enumerate(package.sessions)
    )
    package = replace(package, sessions=sessions)
    with zipfile.ZipFile(BytesIO(build_zip(package, {}))) as archive:
        manifest = json.loads(archive.read(f"{package.package_name}-converted/manifest.json"))

    assert manifest["egress"]["total_acus"] == expected


def test_zipper_rejects_traversal_and_non_contract_part_paths() -> None:
    package = sample_package()
    for path in ("../outside.txt", "/absolute.txt", "misc/file.txt"):
        with pytest.raises(ValueError):
            build_zip(package, {path: b"not safe"})
