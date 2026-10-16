from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from migraai.devin.attachment import parse_batch

_ATTACHMENT = re.compile(r'ATTACHMENT:"([^"]+)"')
_FIXTURE_METADATA = {"qualified_name", "type"}


class ReplayDevinClient:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).resolve()
        index = json.loads((self.directory / "index.json").read_text(encoding="utf-8"))
        self.batches = index.get("batches", [])
        if not isinstance(self.batches, list):
            raise ValueError("Replay index must contain a batches list.")
        self.attachments: dict[str, str] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self._next_attachment = 1
        self._entries: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
        for batch in self.batches:
            outputs = batch.get("outputs", {})
            if not isinstance(outputs, dict):
                raise ValueError("Replay batch outputs must be a role-to-file mapping.")
            for role, relative_path in outputs.items():
                output_path = Path(str(relative_path))
                if output_path.is_absolute() or ".." in output_path.parts:
                    raise ValueError("Replay output paths must stay inside the replay directory.")
                resolved_output = (self.directory / output_path).resolve()
                if self.directory not in resolved_output.parents:
                    raise ValueError("Replay output paths must stay inside the replay directory.")
                payload = json.loads(resolved_output.read_text(encoding="utf-8"))
                entries = payload.get("objects", []) if isinstance(payload, dict) else []
                role_entries = self._entries.setdefault(str(role), {})
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    qualified_name = str(entry.get("qualified_name", "")).casefold()
                    object_type = str(entry.get("type", "")).casefold()
                    if not qualified_name or not object_type:
                        raise ValueError("Replay entries must include qualified_name and type.")
                    key = (qualified_name, object_type)
                    if key in role_entries:
                        raise ValueError("Replay fixture contains duplicate role/object entries.")
                    role_entries[key] = entry

    def upload_attachment(self, name: str, data: bytes | str) -> str:
        del name
        content = data.decode("utf-8") if isinstance(data, bytes) else data
        url = f"replay://attachments/{self._next_attachment}"
        self._next_attachment += 1
        self.attachments[url] = content
        return url

    def create_session(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        max_acu_limit: int | None,
        title: str,
        tags: list[str],
    ) -> tuple[str, str]:
        del schema, max_acu_limit, title
        attachment_match = _ATTACHMENT.search(prompt)
        attachment = self.attachments.get(attachment_match.group(1), "") if attachment_match else ""
        objects = parse_batch(attachment)
        role = self._role(tags)
        role_entries = self._entries.get(role, {})
        structured_entries = []
        matched_keys: list[tuple[str, str]] = []
        for item in objects:
            key = (
                str(item.get("source_qualified_name", item.get("qualified_name", ""))).casefold(),
                str(item.get("object_type", item.get("type", ""))).casefold(),
            )
            captured = role_entries.get(key)
            if captured is None:
                continue
            entry = {key: value for key, value in captured.items() if key not in _FIXTURE_METADATA}
            entry["object_id"] = item["object_id"]
            structured_entries.append(entry)
            matched_keys.append(key)
        stable_key = json.dumps([role, sorted(matched_keys)], separators=(",", ":")).encode()
        session_number = int.from_bytes(hashlib.sha256(stable_key).digest()[:8], "big")
        session_id = f"replay-run1-{role}-{session_number}"
        self.sessions[session_id] = {
            "state": "done",
            "detail": "replayed, nothing sent",
            "structured_output": {"objects": structured_entries},
            "acus": None,
            "replayed": True,
        }
        return session_id, f"replay://run1/{role}/{session_number}"

    def get_session(self, session_id: str) -> dict[str, Any]:
        return dict(self.sessions[session_id])

    def terminate_session(self, session_id: str) -> None:
        del session_id

    def close(self) -> None:
        return None

    @staticmethod
    def _role(tags: list[str]) -> str:
        for role in ("voter_a", "voter_b", "voter_c", "escalation"):
            if role in tags:
                return role
        for tag in tags:
            if tag.startswith("voter-"):
                return f"voter_{tag.removeprefix('voter-').casefold()}"
        raise ValueError("Replay session tags do not identify a voter or escalation role.")
