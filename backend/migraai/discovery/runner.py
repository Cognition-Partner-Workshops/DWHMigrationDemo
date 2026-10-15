from __future__ import annotations

import queue
import threading
from typing import Any

from migraai.discovery.detect import decode_source
from migraai.discovery.pipeline import discover_files
from migraai.discovery.resolver import resolve


class DiscoveryRunner:
    def __init__(self, session: dict[str, Any], audit: Any) -> None:
        self.session = session
        self.audit = audit
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.RLock()
        self._work_lock = threading.RLock()
        self._closed = False
        self._status: dict[str, Any] = {
            "state": "idle",
            "files_total": 0,
            "files_completed": 0,
            "current_file": None,
        }
        self._thread = threading.Thread(target=self._run, daemon=True, name="migraai-discovery")
        self._thread.start()

    def submit(self, upload_id: str) -> None:
        if not self._closed:
            self._queue.put(upload_id)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _run(self) -> None:
        while True:
            upload_id = self._queue.get()
            if upload_id is None:
                self._queue.task_done()
                return
            try:
                self._discover()
            except Exception:
                with self._lock:
                    self._status["state"] = "failed"
                    self._status["current_file"] = None
            finally:
                self._queue.task_done()

    def _discover(self) -> None:
        with self._work_lock:
            with self.session["upload_lock"]:
                upload_snapshot = dict(self.session.get("uploads", {}))
            rows = [
                row
                for row in upload_snapshot.values()
                if row.status != "rejected" and row.store_path
            ]
            with self._lock:
                self._status.update(
                    state="running",
                    files_total=len(rows),
                    files_completed=0,
                    current_file=None,
                )
            parsed = []
            object_files: dict[int, str] = {}
            previews: dict[str, str] = {}
            issues = 0
            errors: list[str] = []
            for index, row in enumerate(rows, 1):
                with self._lock:
                    self._status["current_file"] = row.rel_path
                row.status = "parsing"
                try:
                    data = self.session["store"].read(row.store_path)
                    text, _, _ = decode_source(data)
                    previews[row.rel_path] = text
                    file_objects, _ = discover_files({row.rel_path: data}, self.session["source"])
                    file_objects = [item for item in file_objects if item.type != "external"]
                    parsed.extend(file_objects)
                    object_files.update({id(item): row.rel_path for item in file_objects})
                    row.object_count = len(file_objects)
                    row.status = (
                        "unparsed"
                        if any(item.status == "unparsed" for item in file_objects)
                        else "partial"
                        if any(item.status == "partial" for item in file_objects)
                        else "done"
                    )
                    issues += sum(len(item.issues) for item in file_objects)
                except Exception:
                    row.status = "unparsed"
                    row.message = "File could not be parsed."
                    issues += 1
                    errors.append(row.id)
                self.session["inventory"].add_upload(row)
                with self._lock:
                    self._status["files_completed"] = index
            all_objects, edges = resolve(
                parsed,
                self.session["source"],
                object_files=object_files,
            )
            self.session["inventory"].replace_discovery(
                all_objects,
                edges,
                upload_snapshot,
                previews,
            )
            for upload_id in errors:
                self.session["inventory"].add_issue(
                    upload_id,
                    severity="error",
                    code="discovery.parse_error",
                    message="File could not be parsed.",
                )
            with self._lock:
                self._status["state"] = "done"
                self._status["current_file"] = None
            self.audit.append(
                "system",
                "discovery.complete",
                self.session["session_id"],
                {
                    "file_count": len(rows),
                    "object_count": len(parsed),
                    "edge_count": len(edges),
                    "issue_count": issues,
                },
            )

    def close(self) -> None:
        with self._work_lock:
            self._closed = True
            self._queue.put(None)
            self._thread.join(timeout=35)
