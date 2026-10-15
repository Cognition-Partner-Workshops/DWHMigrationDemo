from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

_BATCH_MARKER = "<!-- MigraAI batch v1 -->"
_OBJECT_MARKER = "<!-- migraai-object -->"
_JSON_FENCE = "```json"
_SOURCE_HEADER = "#### Source"


def _line_value(line: str) -> str:
    return line.rstrip("\r\n")


def _fence_for(text: str) -> str:
    backtick_runs = [len(match.group()) for match in re.finditer(r"`+", text)]
    return "`" * max(3, max(backtick_runs, default=0) + 1)


def render_batch(objects: Iterable[Mapping[str, Any]]) -> str:
    rendered = f"{_BATCH_MARKER}\n\n"
    for obj in objects:
        item = dict(obj)
        object_id = item.pop("object_id", None)
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("Every batch object must have a non-empty object_id.")
        source_text = item.pop("source_text", item.pop("text", ""))
        if not isinstance(source_text, str):
            raise TypeError("Batch source text must be a string.")
        language = re.sub(r"[^A-Za-z0-9_+.-]", "", str(item.get("language", "text"))) or "text"
        fence = _fence_for(source_text)
        metadata = {
            "object_id": object_id,
            "metadata": item,
            "source_trailing_newline": source_text.endswith("\n"),
        }
        rendered += (
            f"{_OBJECT_MARKER}\n"
            f"### Object metadata\n"
            f"{_JSON_FENCE}\n"
            f"{json.dumps(metadata, ensure_ascii=False, sort_keys=True)}\n"
            "```\n"
            f"{_SOURCE_HEADER}\n"
            f"{fence}{language}\n"
        )
        rendered += source_text
        if not source_text.endswith("\n"):
            rendered += "\n"
        rendered += f"{fence}\n\n"
    return rendered


def parse_batch(md: str) -> list[dict[str, Any]]:
    lines = md.splitlines(keepends=True)
    objects: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if _line_value(lines[index]) != _OBJECT_MARKER:
            index += 1
            continue
        index += 1
        if index >= len(lines) or _line_value(lines[index]) != "### Object metadata":
            raise ValueError("Batch object metadata header is missing.")
        index += 1
        if index >= len(lines) or _line_value(lines[index]) != _JSON_FENCE:
            raise ValueError("Batch object metadata must be JSON.")
        index += 1
        metadata_lines: list[str] = []
        while index < len(lines) and _line_value(lines[index]) != "```":
            metadata_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            raise ValueError("Batch object metadata block is not closed.")
        index += 1
        try:
            envelope = json.loads("".join(metadata_lines))
        except json.JSONDecodeError as error:
            raise ValueError("Batch object metadata is invalid JSON.") from error
        if index >= len(lines) or _line_value(lines[index]) != _SOURCE_HEADER:
            raise ValueError("Batch object source header is missing.")
        index += 1
        if index >= len(lines):
            raise ValueError("Batch object source fence is missing.")
        fence_line = _line_value(lines[index])
        match = re.fullmatch(r"(`{3,})[A-Za-z0-9_+.-]*", fence_line)
        if match is None:
            raise ValueError("Batch object source fence is invalid.")
        fence = match.group(1)
        index += 1
        source_lines: list[str] = []
        while index < len(lines) and _line_value(lines[index]) != fence:
            source_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            raise ValueError("Batch object source block is not closed.")
        source_text = "".join(source_lines)
        if not envelope.get("source_trailing_newline") and source_text.endswith("\n"):
            source_text = source_text[:-1]
        index += 1
        object_id = envelope.get("object_id")
        metadata = envelope.get("metadata")
        if not isinstance(object_id, str) or not isinstance(metadata, dict):
            raise ValueError("Batch object metadata has an invalid shape.")
        objects.append({"object_id": object_id, **metadata, "source_text": source_text})
    if not objects and _BATCH_MARKER not in md:
        raise ValueError("Batch attachment marker is missing.")
    return objects
