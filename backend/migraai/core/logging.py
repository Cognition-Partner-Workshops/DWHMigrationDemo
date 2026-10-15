from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AccessLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(
        self,
        *,
        method: str,
        path: str,
        status: int,
        duration_ms: float,
        session_id: str | None,
    ) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "method": method,
            "path": path.split("?", 1)[0],
            "status": status,
            "duration_ms": round(duration_ms, 2),
            "session_id": session_id[:8] if session_id else None,
        }
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as log_file:
            log_file.write(json.dumps(record, separators=(",", ":")) + "\n")
