from __future__ import annotations

import hashlib
import io
import stat
import threading
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import BinaryIO

from migraai.discovery.detect import detect_file

MAX_SESSION_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
CHUNK_SIZE = 64 * 1024
ALLOWED_EXTENSIONS = frozenset(
    {
        ".sql",
        ".ddl",
        ".txt",
        ".zip",
        ".xml",
        ".dtsx",
        ".dsx",
        ".py",
        ".sh",
        ".scala",
        ".json",
        ".yaml",
        ".yml",
        ".infa",
    }
)


@dataclass(slots=True)
class UploadRow:
    id: str
    rel_path: str
    size: int
    detected_type: str
    status: str = "pending"
    message: str | None = None
    selected: bool = True
    object_count: int = 0
    digest: str = ""
    store_path: str = ""
    archive_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "rel_path": self.rel_path,
            "size": self.size,
            "detected_type": self.detected_type,
            "status": self.status,
            "message": self.message,
            "selected": self.selected,
            "object_count": self.object_count,
        }


class UploadRejected(Exception):
    pass


class _BoundedReader:
    def __init__(
        self,
        source: BinaryIO,
        *,
        max_bytes: int,
        compressed_size: int | None = None,
        session: dict | None = None,
    ) -> None:
        self.source = source
        self.max_bytes = max_bytes
        self.compressed_size = compressed_size
        self.session = session
        self.total = 0
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self.source.read(min(size, CHUNK_SIZE) if size >= 0 else CHUNK_SIZE)
        self.total += len(chunk)
        self.digest.update(chunk)
        if self.session is not None:
            self.session["zip_uncompressed_bytes"] = self.session.get(
                "zip_uncompressed_bytes", 0
            ) + len(chunk)
        if self.total > self.max_bytes:
            raise UploadRejected("Upload size limit exceeded.")
        if (
            self.compressed_size is not None
            and self.total > self.compressed_size * MAX_COMPRESSION_RATIO
        ):
            raise UploadRejected("ZIP entry compression ratio exceeds 200:1.")
        return chunk


def _extension(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].casefold() if "." in name else ""


def _safe_zip_path(name: str) -> str:
    normalized = name.replace("\\", "/")
    posix, windows = PurePosixPath(normalized), PureWindowsPath(name)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {".", ".."} for part in posix.parts)
    ):
        raise UploadRejected("ZIP entry path is not allowed.")
    return str(posix)


def _store_row(
    rel_path: str,
    source: BinaryIO,
    session: dict,
    *,
    max_bytes: int,
    compressed_size: int | None = None,
    archive_id: str | None = None,
    count_zip_bytes: bool = False,
) -> UploadRow:
    row_id = uuid.uuid4().hex
    store_path = f"uploads/{row_id}"
    reader = _BoundedReader(
        source,
        max_bytes=max_bytes,
        compressed_size=compressed_size,
        session=session if count_zip_bytes else None,
    )
    session["store"].write_stream(store_path, reader)
    data = session["store"].read(store_path)
    detection = detect_file(rel_path, data, session["source"])
    row = UploadRow(
        row_id,
        rel_path,
        reader.total,
        detection.detected_type,
        message="; ".join(detection.issues) or None,
        digest=reader.digest.hexdigest(),
        store_path=store_path,
        archive_id=archive_id,
    )
    session.setdefault("uploads", {})[row.id] = row
    return row


def _reject(rel_path: str, size: int, message: str, session: dict) -> UploadRow:
    row = UploadRow(
        uuid.uuid4().hex,
        rel_path,
        size,
        "unsupported",
        status="rejected",
        message=message,
    )
    session.setdefault("uploads", {})[row.id] = row
    return row


def _hash_stream(source: BinaryIO, max_bytes: int) -> tuple[int, str]:
    source.seek(0)
    reader = _BoundedReader(source, max_bytes=max_bytes)
    while reader.read(CHUNK_SIZE):
        pass
    return reader.total, reader.digest.hexdigest()


def _zip_entries(
    archive: zipfile.ZipFile,
    prefix: str,
    session: dict,
    *,
    depth: int,
    archive_id: str,
) -> list[UploadRow]:
    infos = archive.infolist()
    session["zip_entries_seen"] = session.get("zip_entries_seen", 0) + len(infos)
    if session["zip_entries_seen"] > MAX_ZIP_ENTRIES:
        return [_reject(prefix, 0, "ZIP contains more than 10,000 entries.", session)]
    rows: list[UploadRow] = []
    for info in infos:
        rel_path = f"{prefix}/{info.filename}"
        row = None
        try:
            entry_path = _safe_zip_path(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UploadRejected("ZIP symlink entries are not allowed.")
            session["store"]._resolve(f"zip-check/{archive_id}/{entry_path}")
            if info.is_dir():
                continue
            if _extension(entry_path) not in ALLOWED_EXTENSIONS:
                raise UploadRejected("File extension is not allowed.")
            if _extension(entry_path) == ".zip" and depth >= 2:
                raise UploadRejected("Nested ZIP depth exceeds one level.")
            if info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
                raise UploadRejected("ZIP entry compression ratio exceeds 200:1.")
            remaining = MAX_ZIP_UNCOMPRESSED - session.get("zip_uncompressed_bytes", 0)
            with archive.open(info) as member:
                row = _store_row(
                    f"{prefix}/{entry_path}",
                    member,
                    session,
                    max_bytes=remaining,
                    compressed_size=info.compress_size,
                    archive_id=archive_id,
                    count_zip_bytes=True,
                )
            if _extension(entry_path) == ".zip":
                nested = session["store"].read(row.store_path)
                with zipfile.ZipFile(io.BytesIO(nested)) as nested_archive:
                    rows.extend(
                        _zip_entries(
                            nested_archive,
                            row.rel_path,
                            session,
                            depth=depth + 1,
                            archive_id=archive_id,
                        )
                    )
                session["store"].delete(row.store_path)
                session["uploads"].pop(row.id, None)
            else:
                rows.append(row)
        except (UploadRejected, zipfile.BadZipFile, OSError, ValueError) as error:
            if row is not None and row.store_path:
                session["store"].delete(row.store_path)
                session["uploads"].pop(row.id, None)
            rows.append(_reject(rel_path, info.file_size, str(error), session))
    return rows


def ingest_file(filename: str, source: BinaryIO, session: dict) -> list[UploadRow]:
    lock = session.setdefault("upload_lock", threading.RLock())
    with lock:
        name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        extension = _extension(name)
        used = session.get("upload_bytes_received", 0)
        remaining = max(0, MAX_SESSION_BYTES - used)
        try:
            size, digest = _hash_stream(source, remaining)
        except UploadRejected as error:
            row = _reject(name, remaining + 1, str(error), session)
            session["upload_bytes_received"] = MAX_SESSION_BYTES + 1
            return [row]
        session["upload_bytes_received"] = used + size
        if extension not in ALLOWED_EXTENSIONS:
            return [_reject(name, size, "File extension is not allowed.", session)]
        if extension != ".zip":
            source.seek(0)
            row = _store_row(name, source, session, max_bytes=size)
            row.digest = digest
            return [row]
        try:
            source.seek(0)
            with zipfile.ZipFile(source) as archive:
                rows = _zip_entries(archive, name, session, depth=1, archive_id=uuid.uuid4().hex)
                return rows or [_reject(name, size, "ZIP contains no files.", session)]
        except (zipfile.BadZipFile, OSError, ValueError):
            return [_reject(name, size, "Invalid ZIP archive.", session)]
