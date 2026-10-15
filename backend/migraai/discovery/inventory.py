from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from typing import Any

from migraai.discovery.objects import ParsedObject
from migraai.discovery.pipeline import DiscoveryEdge
from migraai.discovery.uploads import UploadRow


class Inventory:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.connection.executescript(
            """
            CREATE TABLE upload (
                id TEXT PRIMARY KEY, rel_path TEXT NOT NULL, size INTEGER NOT NULL,
                detected_type TEXT NOT NULL, status TEXT NOT NULL, message TEXT,
                selected INTEGER NOT NULL, object_count INTEGER NOT NULL,
                digest TEXT NOT NULL, store_path TEXT NOT NULL,
                source_lines INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE object (
                id TEXT PRIMARY KEY, upload_id TEXT, file TEXT NOT NULL, type TEXT NOT NULL,
                schema_name TEXT, name TEXT NOT NULL, qualified_name TEXT NOT NULL,
                status TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
                source_preview TEXT NOT NULL, external INTEGER NOT NULL
            );
            CREATE TABLE dependency (
                id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                kind TEXT NOT NULL, external INTEGER NOT NULL
            );
            CREATE TABLE issue (
                id INTEGER PRIMARY KEY, upload_id TEXT, object_id TEXT, severity TEXT NOT NULL,
                code TEXT NOT NULL, message TEXT NOT NULL
            );
            """
        )

    def add_upload(self, row: UploadRow) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT OR REPLACE INTO upload
                (id,rel_path,size,detected_type,status,message,selected,object_count,
                digest,store_path,source_lines) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row.id,
                    row.rel_path,
                    row.size,
                    row.detected_type,
                    row.status,
                    row.message,
                    int(row.selected),
                    row.object_count,
                    row.digest,
                    row.store_path,
                    0,
                ),
            )

    def upload_rows(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {**dict(row), "selected": bool(row["selected"])}
                for row in self.connection.execute("SELECT * FROM upload ORDER BY rowid")
            ]

    def set_selected(self, upload_id: str, selected: bool) -> bool:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE upload SET selected=? WHERE id=?", (int(selected), upload_id)
            )
            return cursor.rowcount > 0

    def delete_upload(self, upload_id: str) -> dict[str, Any] | None:
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT * FROM upload WHERE id=?", (upload_id,)
            ).fetchone()
            if row is None:
                return None
            self.connection.execute(
                """DELETE FROM dependency WHERE source_id IN
                (SELECT id FROM object WHERE upload_id=?) OR target_id IN
                (SELECT id FROM object WHERE upload_id=?)""",
                (upload_id, upload_id),
            )
            self.connection.execute("DELETE FROM object WHERE upload_id=?", (upload_id,))
            self.connection.execute("DELETE FROM issue WHERE upload_id=?", (upload_id,))
            self.connection.execute("DELETE FROM upload WHERE id=?", (upload_id,))
            return dict(row)

    def replace_discovery(
        self,
        objects: list[ParsedObject],
        edges: list[DiscoveryEdge],
        uploads: dict[str, UploadRow],
        previews: dict[str, str],
    ) -> None:
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM dependency")
            self.connection.execute("DELETE FROM issue")
            self.connection.execute("DELETE FROM object")
            for upload in uploads.values():
                source_lines = len(previews.get(upload.rel_path, "").splitlines())
                self.connection.execute(
                    "UPDATE upload SET source_lines=? WHERE id=?",
                    (source_lines, upload.id),
                )
                if upload.message and upload.status != "rejected":
                    self.connection.execute(
                        """INSERT INTO issue(upload_id,severity,code,message)
                        VALUES(?,?,?,?)""",
                        (upload.id, "warning", "upload.detect_warning", upload.message),
                    )
            ids: dict[tuple[str, str], str] = {}
            name_ids: dict[str, list[str]] = {}
            for item in objects:
                upload = next(
                    (row for row in uploads.values() if row.rel_path == item.file_path), None
                )
                object_id = uuid.uuid4().hex
                ids[(item.qualified_name, item.file_path or "")] = object_id
                name_ids.setdefault(item.qualified_name, []).append(object_id)
                self.connection.execute(
                    "INSERT INTO object VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        object_id,
                        upload.id if upload else None,
                        item.file_path or "",
                        item.type,
                        item.schema,
                        item.name,
                        item.qualified_name,
                        item.status,
                        item.start_line,
                        item.end_line,
                        "\n".join(previews.get(item.file_path or "", "").splitlines()[:200]),
                        int(item.type == "external"),
                    ),
                )
                for issue in item.issues:
                    self.connection.execute(
                        """INSERT INTO issue(upload_id,object_id,severity,code,message)
                        VALUES(?,?,?,?,?)""",
                        (
                            upload.id if upload else None,
                            object_id,
                            issue.severity,
                            issue.code,
                            issue.message,
                        ),
                    )
            for edge in edges:
                source_id = next(
                    (object_id for (name, _), object_id in ids.items() if name == edge.source),
                    None,
                )
                target_ids = name_ids.get(edge.target, [])
                target_id = target_ids[0] if target_ids else edge.target
                if source_id:
                    self.connection.execute(
                        "INSERT INTO dependency(source_id,target_id,kind,external) VALUES(?,?,?,?)",
                        (source_id, target_id, edge.kind, int(edge.external)),
                    )

    def page(
        self,
        *,
        query: str = "",
        object_type: str | None = None,
        status: str | None = None,
        file: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        clauses = ["(o.upload_id IS NULL OR u.selected=1)"]
        params: list[Any] = []
        if query:
            clauses.append("(o.name LIKE ? OR o.qualified_name LIKE ?)")
            params.extend((f"%{query}%", f"%{query}%"))
        if object_type:
            clauses.append("o.type=?")
            params.append(object_type)
        if status:
            clauses.append("o.status=?")
            params.append(status)
        if file:
            clauses.append("o.file LIKE ?")
            params.append(f"%{file}%")
        where = " AND ".join(clauses)
        with self.lock:
            total = self.connection.execute(
                f"""SELECT count(*) FROM object o LEFT JOIN upload u ON u.id=o.upload_id
                WHERE {where}""",
                params,
            ).fetchone()[0]
            rows = self.connection.execute(
                f"""SELECT o.*,
                (SELECT count(*) FROM dependency d WHERE d.target_id=o.id) AS upstream_count,
                (SELECT count(*) FROM dependency d WHERE d.source_id=o.id) AS downstream_count
                FROM object o LEFT JOIN upload u ON u.id=o.upload_id
                WHERE {where} ORDER BY o.qualified_name LIMIT ? OFFSET ?""",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def issue_count(self, upload_id: str) -> int:
        with self.lock:
            return int(
                self.connection.execute(
                    "SELECT count(*) FROM issue WHERE upload_id=?", (upload_id,)
                ).fetchone()[0]
            )

    def detail(self, object_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                """SELECT o.* FROM object o LEFT JOIN upload u ON u.id=o.upload_id
                WHERE o.id=? AND (o.upload_id IS NULL OR u.selected=1)""",
                (object_id,),
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            item["issues"] = [
                dict(issue)
                for issue in self.connection.execute(
                    "SELECT severity,code,message FROM issue WHERE object_id=?", (object_id,)
                )
            ]
            item["upstream"] = self._edges(object_id, downstream=False)
            item["downstream"] = self._edges(object_id, downstream=True)
            return item

    def add_issue(
        self,
        upload_id: str,
        *,
        severity: str,
        code: str,
        message: str,
        object_id: str | None = None,
    ) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO issue(upload_id,object_id,severity,code,message) VALUES(?,?,?,?,?)",
                (upload_id, object_id, severity, code, message),
            )

    def _edges(self, object_id: str, *, downstream: bool) -> list[dict[str, Any]]:
        source_col, target_col = (
            ("source_id", "target_id") if downstream else ("target_id", "source_id")
        )
        rows = self.connection.execute(
            f"""SELECT d.kind,d.external,o.id,o.type,o.qualified_name FROM dependency d
            LEFT JOIN object o ON o.id=d.{target_col} WHERE d.{source_col}=?""",
            (object_id,),
        )
        return [dict(row) for row in rows]

    def graph(self, object_id: str, depth: int) -> dict[str, Any]:
        depth = min(3, max(1, depth))
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        frontier = [(object_id, 0)]
        seen: set[str] = set()
        truncated = False
        with self.lock:
            while frontier:
                current, level = frontier.pop(0)
                if current in seen:
                    continue
                seen.add(current)
                row = self.connection.execute(
                    """SELECT o.* FROM object o LEFT JOIN upload u ON u.id=o.upload_id
                    WHERE o.id=? AND (o.upload_id IS NULL OR u.selected=1)""",
                    (current,),
                ).fetchone()
                if row is None:
                    continue
                nodes[current] = dict(row)
                if level >= depth:
                    continue
                related = self.connection.execute(
                    """SELECT d.*,s.qualified_name AS source_name,t.qualified_name AS target_name
                    FROM dependency d LEFT JOIN object s ON s.id=d.source_id
                    LEFT JOIN upload su ON su.id=s.upload_id
                    LEFT JOIN object t ON t.id=d.target_id
                    LEFT JOIN upload tu ON tu.id=t.upload_id
                    WHERE ((d.source_id=? AND (s.upload_id IS NULL OR su.selected=1))
                    OR (d.target_id=? AND (t.upload_id IS NULL OR tu.selected=1)))""",
                    (current, current),
                ).fetchall()
                for edge in related:
                    key = (edge["source_id"], edge["target_id"], edge["kind"])
                    edges[key] = dict(edge)
                    neighbor = (
                        edge["target_id"] if edge["source_id"] == current else edge["source_id"]
                    )
                    if neighbor in seen:
                        continue
                    if len(nodes) + len(frontier) >= 300:
                        truncated = True
                        continue
                    frontier.append((neighbor, level + 1))
        return {
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "truncated": truncated,
        }

    def count_types(self, types: list[str]) -> int:
        if not types:
            return 0
        marks = ",".join("?" for _ in types)
        with self.lock:
            return int(
                self.connection.execute(
                    f"""SELECT count(*) FROM object o JOIN upload u ON u.id=o.upload_id
                    WHERE u.selected=1 AND o.type IN ({marks})""",
                    types,
                ).fetchone()[0]
            )

    def aggregates(self) -> dict[str, Any]:
        with self.lock:
            objects_by_type = {
                str(row["type"]): int(row["count"])
                for row in self.connection.execute(
                    """SELECT o.type,count(*) AS count FROM object o
                    JOIN upload u ON u.id=o.upload_id
                    WHERE u.selected=1 GROUP BY o.type ORDER BY o.type"""
                )
            }
            parse_status_counts = {
                str(row["status"]): int(row["count"])
                for row in self.connection.execute(
                    """SELECT o.status,count(*) AS count FROM object o
                    JOIN upload u ON u.id=o.upload_id
                    WHERE u.selected=1 GROUP BY o.status ORDER BY o.status"""
                )
            }
            files_by_detected_type = {
                str(row["detected_type"]): int(row["count"])
                for row in self.connection.execute(
                    """SELECT detected_type,count(*) AS count FROM upload
                    WHERE selected=1 GROUP BY detected_type ORDER BY detected_type"""
                )
            }
            dependencies_by_kind = {
                str(row["kind"]): int(row["count"])
                for row in self.connection.execute(
                    """SELECT d.kind,count(*) AS count FROM dependency d
                    JOIN object o ON o.id=d.source_id
                    JOIN upload u ON u.id=o.upload_id
                    WHERE u.selected=1 GROUP BY d.kind ORDER BY d.kind"""
                )
            }
            total_dependencies = int(
                self.connection.execute(
                    """SELECT count(*) FROM dependency d
                    JOIN object o ON o.id=d.source_id
                    JOIN upload u ON u.id=o.upload_id
                    WHERE u.selected=1"""
                ).fetchone()[0]
            )
            external_reference_count = int(
                self.connection.execute(
                    """SELECT count(*) FROM dependency d
                    JOIN object o ON o.id=d.source_id
                    JOIN upload u ON u.id=o.upload_id
                    WHERE u.selected=1 AND d.external=1"""
                ).fetchone()[0]
            )
            issues_by_severity = {
                str(row["severity"]): int(row["count"])
                for row in self.connection.execute(
                    """SELECT i.severity,count(*) AS count FROM issue i
                    JOIN upload u ON u.id=i.upload_id
                    WHERE u.selected=1 GROUP BY i.severity ORDER BY i.severity"""
                )
            }
            total_source_lines = int(
                self.connection.execute(
                    "SELECT coalesce(sum(source_lines),0) FROM upload WHERE selected=1"
                ).fetchone()[0]
            )
        return {
            "objects_by_type": objects_by_type,
            "parse_status_counts": parse_status_counts,
            "files_by_detected_type": files_by_detected_type,
            "total_dependencies": total_dependencies,
            "dependencies_by_kind": dependencies_by_kind,
            "external_reference_count": external_reference_count,
            "issues_by_severity": issues_by_severity,
            "total_source_lines": total_source_lines,
        }

    def fingerprint(self) -> str:
        with self.lock:
            aggregates = self.aggregates()
            selected_upload = self.connection.execute(
                "SELECT 1 FROM upload WHERE selected=1 LIMIT 1"
            ).fetchone()
            if selected_upload is None:
                return ""
            canonical = json.dumps(aggregates, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def close(self) -> None:
        with self.lock:
            self.connection.close()
