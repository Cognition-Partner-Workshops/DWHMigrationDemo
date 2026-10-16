from __future__ import annotations

import re
from collections.abc import Iterable, MutableSet
from pathlib import PurePosixPath, PureWindowsPath

from migraai.packaging.model import PackagedObject, PackageInput


def sanitize_package_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("._-")
    sanitized = sanitized[:80].rstrip("._-")
    return sanitized or "migraai"


def default_package_name(
    upload_paths: Iterable[str],
    source: str,
    target: str,
) -> str:
    paths = list(upload_paths)
    if len(paths) == 1:
        path = paths[0].replace("\\", "/").rsplit("/", 1)[-1]
        if path.casefold().endswith(".zip"):
            path = path[:-4]
        else:
            path = PurePosixPath(path).stem
        return sanitize_package_name(path)
    return sanitize_package_name(f"migraai-{source}-to-{target}")


def package_name(
    value: str | None,
    *,
    upload_paths: Iterable[str] = (),
    source: str = "",
    target: str = "",
) -> str:
    if value is not None and value.strip():
        return sanitize_package_name(value.strip())
    return default_package_name(upload_paths, source, target)


def upload_root(rel_path: str) -> str:
    """Return the upload root represented by a relative upload path."""
    normalized = rel_path.replace("\\", "/")
    segments = normalized.split("/")
    for index, segment in enumerate(segments):
        if segment.casefold().endswith(".zip"):
            return "/".join(segments[: index + 1])
    return normalized


def _relative_parts(path: str) -> list[str]:
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
        raise ValueError("Package paths must be relative and may not contain '..'.")
    parts = [part[:-4] if part.casefold().endswith(".zip") else part for part in posix.parts]
    if any(not part for part in parts):
        raise ValueError("Package paths must be relative and may not contain '..'.")
    return parts


def converted_path(
    source_file: str,
    suffix: str,
    taken: MutableSet[str],
) -> str:
    parts = _relative_parts(source_file)
    if not parts or not parts[-1]:
        raise ValueError("A source file path is required.")
    if not suffix:
        suffix = PurePosixPath(parts[-1]).suffix
    if "/" in suffix or "\\" in suffix or ".." in suffix:
        raise ValueError("The converted file suffix is not allowed.")

    filename = parts[-1]
    source_suffix = PurePosixPath(filename).suffix
    stem = filename[: -len(source_suffix)] if source_suffix else filename
    requested_suffix = suffix
    if requested_suffix.startswith("-"):
        match = re.fullmatch(r"(-[A-Za-z0-9._-]*?)(\.[A-Za-z0-9]+)?", requested_suffix)
        if match is None:
            raise ValueError("The converted file suffix is not allowed.")
        marker, extension = match.groups()
        extension = extension or source_suffix
    else:
        extension = requested_suffix if requested_suffix.startswith(".") else f".{requested_suffix}"
        if re.fullmatch(r"\.[A-Za-z0-9._-]+", extension) is None:
            raise ValueError("The converted file suffix is not allowed.")
        marker = "-converted"

    base = "/".join((*parts[:-1], f"{stem}{marker}{extension}"))
    candidate = base
    collision = 2
    while candidate in taken:
        name = PurePosixPath(base).name
        extension = PurePosixPath(name).suffix
        stem = name[: -len(extension)] if extension else name
        candidate = "/".join((*parts[:-1], f"{stem}-{collision}{extension}"))
        collision += 1
    taken.add(candidate)
    return candidate


def _review_reason(item: PackagedObject, package: PackageInput) -> str:
    if item.confidence is not None:
        return (
            f"Confidence {item.confidence:.0%} is below the "
            f"{package.confidence_threshold:.0%} threshold."
        )
    return "Expert review required."


def converted_files(package: PackageInput) -> dict[str, bytes]:
    grouped: dict[str, list[tuple[int, str, int, PackagedObject, str, str]]] = {}
    for item in package.included():
        for index, file in enumerate(item.files):
            if not file.package_path:
                continue
            path = "/".join(_relative_parts(file.package_path))
            content = file.content.rstrip()
            if item.status == "expert_review" and file.language != "json":
                marker = "--" if file.language == "sql" else "#"
                content = f"{marker} NEEDS-REVIEW: {_review_reason(item, package)}" + (
                    f"\n{content}" if content else ""
                )
            grouped.setdefault(path, []).append(
                (
                    item.source_line_start,
                    item.object_id,
                    index,
                    item,
                    file.language,
                    content,
                )
            )

    result: dict[str, bytes] = {}
    for path, chunks in sorted(grouped.items()):
        languages = {chunk[4] for chunk in chunks}
        if len(languages) > 1:
            raise ValueError(f"Conflicting output languages for {path!r}.")
        ordered = sorted(chunks, key=lambda chunk: (chunk[0], chunk[1], chunk[2]))
        text = "\n\n".join(chunk[5] for chunk in ordered).rstrip() + "\n"
        result[path] = text.encode("utf-8")
    return result


__all__ = [
    "converted_files",
    "converted_path",
    "default_package_name",
    "package_name",
    "sanitize_package_name",
    "upload_root",
]
