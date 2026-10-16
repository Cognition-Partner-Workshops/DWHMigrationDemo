from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from migraai.packaging.acu import total_acus
from migraai.packaging.model import PackageInput
from migraai.packaging.naming import converted_files, sanitize_package_name


def _zip_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(path)
    if (
        not normalized
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError("ZIP paths must be relative and may not contain '..'.")
    return str(posix)


def _timestamp(value: str) -> tuple[int, int, int, int, int, int]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Package created_at must be an ISO-8601 timestamp.") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    year = min(2107, max(1980, parsed.year))
    second = parsed.second - parsed.second % 2
    return year, parsed.month, parsed.day, parsed.hour, parsed.minute, second


def _object_manifest(item: Any) -> dict[str, Any]:
    return {
        "id": item.object_id,
        "source_name": item.source_qualified_name,
        "target_name": item.target_qualified_name,
        "type": item.object_type,
        "status": item.status,
        "confidence": item.confidence,
        "files": [f"converted/{file.package_path}" for file in item.files],
    }


def _manifest(package: PackageInput, files: dict[str, bytes]) -> bytes:
    counts: dict[str, int] = {}
    for item in package.objects:
        counts[item.status] = counts.get(item.status, 0) + 1
    excluded = [
        {
            "id": item.object_id,
            "name": item.source_qualified_name,
            "status": item.status,
            "reason": item.error or f"Object status is {item.status}.",
        }
        for item in package.excluded()
    ]
    review = [
        {
            "id": item.object_id,
            "name": item.source_qualified_name,
            "files": [f"converted/{file.package_path}" for file in item.files],
            "json_needs_review_flag": any(file.language == "json" for file in item.files),
        }
        for item in package.objects
        if item.status == "expert_review"
    ]
    content = {
        "package_name": package.package_name,
        "run_id": package.run_id,
        "source_platform": package.source_platform,
        "target_platform": package.target_platform,
        "created_at": package.created_at,
        "counts": {
            **counts,
            "total": len(package.objects),
            "included": len(package.included()),
            "excluded": len(package.excluded()),
            "external": len(package.external),
        },
        "egress": (
            {"total_acus": total_acus(package.sessions)} if package.egress is not None else None
        ),
        "objects": [_object_manifest(item) for item in package.objects],
        "excluded": excluded,
        "external": list(package.external),
        "needs_review": review,
        "sha256": {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())},
    }
    if package.base_run_id is not None:
        content["base_run_id"] = package.base_run_id
        content["reconverted_object_ids"] = list(package.reconverted_object_ids)
    if package.sessions and (
        package.base_run_id is not None or any(s.replayed for s in package.sessions)
    ):
        content["sessions"] = [
            {
                "run_id": item.run_id,
                "role": item.role,
                "batch_index": item.batch_index,
                "state": "replayed, nothing sent" if item.replayed else item.state,
                "acus": item.acus,
                "url": item.url,
            }
            for item in package.sessions
        ]
    return (
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def build_zip(package: PackageInput, parts: dict[str, bytes]) -> bytes:
    """Build a deterministic archive from a frozen package and supplied generator parts."""
    root = f"{sanitize_package_name(package.package_name)}-converted"
    entries: dict[str, bytes] = {}

    for path, content in converted_files(package).items():
        entries[_zip_path(f"converted/{path}")] = content
    for path, content in parts.items():
        relative = _zip_path(path)
        if relative == "manifest.json" or relative.startswith("converted/"):
            raise ValueError(f"Generator part uses a reserved ZIP path: {relative}")
        if not relative.startswith(("mapping/", "execution/", "report/")):
            raise ValueError(f"Generator part is outside the package folders: {relative}")
        if relative in entries:
            raise ValueError(f"Duplicate ZIP path: {relative}")
        if not isinstance(content, bytes):
            raise TypeError(f"ZIP part {relative!r} must be bytes.")
        entries[relative] = content

    manifest = _manifest(package, entries)
    entries["manifest.json"] = manifest
    timestamp = _timestamp(package.created_at)
    target_folder = sanitize_package_name(package.target_platform)
    directories = {
        f"{root}/converted/",
        f"{root}/mapping/",
        f"{root}/mapping/csv/",
        f"{root}/execution/{target_folder}/",
        f"{root}/report/",
    }
    archive_entries = {f"{root}/{path}": data for path, data in entries.items()}
    archive_entries.update({name: b"" for name in directories})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(archive_entries.items()):
            info = zipfile.ZipInfo(name, date_time=timestamp)
            info.create_system = 3
            if name.endswith("/"):
                info.external_attr = (0o40755 << 16) | 0x10
                archive.writestr(info, content)
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                info.flag_bits = 0
                archive.writestr(
                    info,
                    content,
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
    return output.getvalue()


__all__ = ["build_zip"]
