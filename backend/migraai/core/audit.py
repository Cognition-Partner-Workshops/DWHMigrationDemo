from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


def _canonical_json(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(prev_hash: str, record_without_hash: dict[str, Any]) -> str:
    value = prev_hash + _canonical_json(record_without_hash)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_chain(path: Path) -> tuple[bool, int | None]:
    previous = GENESIS_HASH
    expected_seq = 1
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return True, None
    for line in lines:
        try:
            record = json.loads(line)
            sequence = int(record["seq"])
            saved_hash = str(record["hash"])
            record_without_hash = {key: value for key, value in record.items() if key != "hash"}
            if (
                sequence != expected_seq
                or record.get("prev_hash") != previous
                or saved_hash != _record_hash(previous, record_without_hash)
            ):
                return False, sequence
            previous = saved_hash
            expected_seq += 1
        except (ValueError, TypeError, KeyError):
            return False, expected_seq
    return True, None


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 1
        self._prev_hash = GENESIS_HASH
        if self.path.exists():
            lines = self.path.read_text(encoding="utf-8").splitlines()
            if lines:
                last = json.loads(lines[-1])
                self._seq = int(last["seq"]) + 1
                self._prev_hash = str(last["hash"])

    def append(
        self,
        actor: str,
        action: str,
        target: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            record: dict[str, Any] = {
                "seq": self._seq,
                "ts": datetime.now(UTC).isoformat(),
                "actor": actor,
                "action": action,
                "target": target,
                "details": details or {},
                "prev_hash": self._prev_hash,
            }
            digest = _record_hash(self._prev_hash, record)
            complete = {**record, "hash": digest}
            with self.path.open("a", encoding="utf-8", newline="\n") as audit_file:
                audit_file.write(_canonical_json(complete) + "\n")
            self._seq += 1
            self._prev_hash = digest
            return complete
