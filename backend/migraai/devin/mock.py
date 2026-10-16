from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import sqlglot

from migraai.devin.attachment import parse_batch

_SOURCE_DIALECTS = {
    "teradata": "teradata",
    "oracle": "oracle",
    "tsql": "tsql",
    "t-sql": "tsql",
    "sqlserver": "tsql",
    "snowflake": "snowflake",
    "hive": "hive",
    "spark": "spark",
}
_TARGET_DIALECTS = {
    "databricks": "databricks",
    "redshift": "redshift",
    "snowflake": "snowflake",
    "fabric": "fabric",
    "bigquery": "bigquery",
    "synapse": "tsql",
    "tsql": "tsql",
    "clickhouse": "clickhouse",
}
_LANGUAGE_EXTENSIONS = {"sql": "sql", "python": "py", "json": "json", "yaml": "yaml"}


@dataclass(slots=True)
class MockScenario:
    fail_voters: set[str] = field(default_factory=set)
    timeout_voters: set[str] = field(default_factory=set)
    blocked_voters: set[str] = field(default_factory=set)
    blocked_with_output_voters: set[str] = field(default_factory=set)
    missing_object_ids: dict[str, set[str]] = field(default_factory=dict)
    different_candidate_per_stance: bool = False
    create_5xx_once: bool = False


class MockDevinClient:
    def __init__(
        self,
        scenario: MockScenario | None = None,
        *,
        source_dialect: str | None = None,
        target_dialect: str | None = None,
    ) -> None:
        self.scenario = scenario or MockScenario()
        self.source_dialect = source_dialect
        self.target_dialect = target_dialect
        self.attachments: dict[str, str] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.termination_calls: list[str] = []
        self.closed = False
        self._next_attachment = 1
        self._next_session = 1
        self._create_5xx_raised = False

    def upload_attachment(self, name: str, data: bytes | str) -> str:
        content = data.decode("utf-8") if isinstance(data, bytes) else data
        url = f"mock://attachments/{self._next_attachment}"
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
        try:
            delay_s = float(os.environ.get("MIGRAAI_TEST_MOCK_SESSION_DELAY_S", "0"))
        except ValueError:
            delay_s = 0
        if delay_s > 0:
            time.sleep(min(delay_s, 5))
        if self.scenario.create_5xx_once and not self._create_5xx_raised:
            self._create_5xx_raised = True
            request = httpx.Request("POST", "https://mock.local/sessions")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("mock create failure", request=request, response=response)
        attachment_match = re.search(r'ATTACHMENT:"([^"]+)"', prompt)
        attachment = self.attachments.get(attachment_match.group(1), "") if attachment_match else ""
        try:
            objects = parse_batch(attachment)
        except ValueError:
            objects = []
        voter = next(
            (tag.removeprefix("voter-") for tag in tags if tag.startswith("voter-")),
            "default",
        )
        source, target = self._dialects(prompt)
        structured_output = self._convert(objects, voter, source, target)
        if voter in self.scenario.missing_object_ids or "*" in self.scenario.missing_object_ids:
            missing_ids = self.scenario.missing_object_ids.get(voter, set()) | (
                self.scenario.missing_object_ids.get("*", set())
            )
            structured_output["objects"] = [
                item
                for item in structured_output["objects"]
                if item["object_id"] not in missing_ids
            ]
        state = "done"
        output: dict[str, Any] | None = structured_output
        detail = "finished"
        if voter in self.scenario.fail_voters:
            state, output, detail = "failed", None, "Mock voter failure."
        elif voter in self.scenario.timeout_voters:
            state, output, detail = "working", None, ""
        elif voter in self.scenario.blocked_voters:
            state, output, detail = "blocked", None, "Mock session is blocked."
        elif voter in self.scenario.blocked_with_output_voters:
            state, detail = "blocked", "Mock session is blocked with output."
        session_id = f"mock-session-{self._next_session}"
        self._next_session += 1
        url = f"mock://sessions/{session_id}"
        self.sessions[session_id] = {
            "state": state,
            "detail": detail,
            "structured_output": output,
        }
        return session_id, url

    def get_session(self, session_id: str) -> dict[str, Any]:
        return dict(self.sessions[session_id])

    def terminate_session(self, session_id: str) -> None:
        self.termination_calls.append(session_id)

    def close(self) -> None:
        self.closed = True

    def _dialects(self, prompt: str) -> tuple[str | None, str | None]:
        route = re.search(
            r"\bfrom\s+([A-Za-z][A-Za-z0-9_-]*)\s+to\s+([A-Za-z][A-Za-z0-9_-]*)",
            prompt,
            re.IGNORECASE,
        )
        source = self.source_dialect or (route.group(1).casefold() if route else None)
        target = self.target_dialect or (route.group(2).casefold() if route else None)
        source = _SOURCE_DIALECTS.get(source.casefold(), source.casefold()) if source else None
        target = _TARGET_DIALECTS.get(target.casefold(), target.casefold()) if target else None
        return source, target

    def _convert(
        self,
        objects: list[dict[str, Any]],
        voter: str,
        source: str | None,
        target: str | None,
    ) -> dict[str, Any]:
        converted_objects: list[dict[str, Any]] = []
        for obj in objects:
            object_id = obj["object_id"]
            language = str(obj.get("language", "sql")).casefold()
            raw_files = obj.get("files")
            if isinstance(raw_files, list) and raw_files:
                source_files = [file for file in raw_files if isinstance(file, dict)]
            elif isinstance(obj.get("output_formats"), list) and obj["output_formats"]:
                source_files = []
                for item in obj["output_formats"]:
                    if not isinstance(item, dict):
                        continue
                    extension = _LANGUAGE_EXTENSIONS.get(
                        str(item.get("language", "sql")),
                        "sql",
                    )
                    source_files.append(
                        {
                            "rel_path": item.get("rel_path")
                            or item.get("path")
                            or f"{object_id}.{extension}",
                            "language": item.get("language", language),
                            "content": item.get("skeleton", obj.get("source_text", "")),
                        }
                    )
            else:
                source_files = [
                    {
                        "rel_path": obj.get("rel_path"),
                        "language": language,
                        "content": obj.get("source_text", ""),
                    }
                ]
            output_files = []
            for source_file in source_files:
                file_language = str(source_file.get("language", language)).casefold()
                source_text = str(source_file.get("content", obj.get("source_text", "")))
                converted = self._transpile(source_text, file_language, source, target)
                comment = "#" if file_language in {"python", "yaml"} else "--"
                reason = "Preserved the source text using the deterministic mock conversion."
                converted_content = (
                    f"{comment} Converted: Generated a deterministic {target or 'target'} draft.\n"
                    f"{comment} Reason: {reason}\n"
                )
                if self.scenario.different_candidate_per_stance:
                    converted_content += f"{comment} Candidate: {voter}\n"
                converted_content += converted
                rel_path = source_file.get("rel_path") or obj.get("rel_path")
                if not rel_path:
                    extension = _LANGUAGE_EXTENSIONS.get(file_language, "sql")
                    rel_path = f"{object_id}.{extension}"
                if self.scenario.different_candidate_per_stance:
                    path, extension = (
                        str(rel_path).rsplit(".", 1)
                        if "." in str(rel_path)
                        else (str(rel_path), "")
                    )
                    rel_path = (
                        f"{path}.{voter.lower()}.{extension}"
                        if extension
                        else f"{path}.{voter.lower()}"
                    )
                output_files.append(
                    {
                        "rel_path": str(rel_path),
                        "language": (
                            file_language if file_language in _LANGUAGE_EXTENSIONS else "sql"
                        ),
                        "content": converted_content,
                    }
                )
            converted_objects.append(
                {
                    "object_id": object_id,
                    "files": output_files,
                    "explanations": ["Deterministic mock conversion."],
                    "assumptions": [],
                    "unsupported": [],
                    "self_confidence": 0.5 if target is None else 0.9,
                }
            )
        return {"objects": converted_objects}

    @staticmethod
    def _transpile(
        source_text: str,
        language: str,
        source: str | None,
        target: str | None,
    ) -> str:
        if (
            language != "sql"
            or source not in _SOURCE_DIALECTS.values()
            or target not in set(_TARGET_DIALECTS.values())
        ):
            return source_text
        try:
            converted = sqlglot.transpile(source_text, read=source, write=target)
            return "\n".join(converted) if converted else source_text
        except Exception:
            return source_text
