"""Shared error recovery for Python-family sources.

``ast.parse`` is re-run after blanking the top-level statement block that
contains each SyntaxError, up to 20 rounds. Each blanked block becomes an
``ast.Unparsed`` node plus an error issue; the module object owning it is
``partial``. If the file still fails after the limit the caller produces a
file-level ``unparsed`` object.
"""

from __future__ import annotations

import ast as pyast
from dataclasses import dataclass, field

from migraai.discovery import ast
from migraai.etl.python.common import redact, span

MAX_ROUNDS = 20


@dataclass(slots=True)
class RecoveredModule:
    tree: pyast.Module | None
    recovered: list[ast.Unparsed] = field(default_factory=list)
    issues: list[ast.Issue] = field(default_factory=list)


def _top_level_block(lines: list[str], error_line: int) -> tuple[int, int]:
    """Return (start, end) 1-based inclusive line range of the top-level block
    containing ``error_line``: from the nearest line <= error_line that starts
    at column 0 and is not blank/comment, to the line before the next such line.
    """
    start = error_line
    while start > 1:
        text = lines[start - 1]
        if text and not text[0].isspace() and not text.lstrip().startswith("#"):
            break
        start -= 1
    end = len(lines)
    for index in range(start + 1, len(lines) + 1):
        text = lines[index - 1]
        if text and not text[0].isspace() and not text.lstrip().startswith("#"):
            end = index - 1
            break
    return start, end


def _bracket_depth(lines: list[str], upto: int) -> int:
    """Net open-bracket depth over the first ``upto`` lines, skipping quoted
    strings and comments. Heuristic only — good enough for locating which
    earlier physical line opened a bracket that was never closed."""
    depth = 0
    for line in lines[:upto]:
        index = 0
        while index < len(line):
            char = line[index]
            if char == "#":
                break
            if char in "\"'":
                quote = char * 3 if line.startswith(char * 3, index) else char
                close = line.find(quote, index + len(quote))
                if close == -1:
                    break  # unterminated string: treat rest of line as string
                index = close + len(quote)
                continue
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            index += 1
    return depth


def _line_range(lines: list[str], error_line: int) -> tuple[int, int] | None:
    """Smallest 1-based line range covering the logical statement containing
    ``error_line``: extended backwards across bracket continuations and ``\\``
    line joins, and forwards over deeper-indented lines."""
    line = min(max(error_line, 1), len(lines))
    start = line
    while start > 1 and (
        _bracket_depth(lines, start - 1) > 0 or lines[start - 1].rstrip().endswith("\\")
    ):
        start -= 1
    indent = len(lines[start - 1]) - len(lines[start - 1].lstrip())
    end = start
    while end < len(lines):
        nxt = lines[end]
        stripped = nxt.strip()
        if stripped and not stripped.startswith("#"):
            if len(nxt) - len(nxt.lstrip()) <= indent:
                break
        end += 1
    if not lines[start - 1].strip() and not lines[start:end]:
        return None
    return start, end


def parse_python_recovering(text: str, tool: str) -> RecoveredModule:
    result = RecoveredModule(tree=None)
    current = text
    seen_ranges: set[tuple[int, int]] = set()
    for _ in range(MAX_ROUNDS):
        try:
            result.tree = pyast.parse(current)
            return result
        except SyntaxError as error:
            line = error.lineno or 1
            lines = current.split("\n")
            line_range = _line_range(lines, line)
            if line_range is not None and line_range not in seen_ranges:
                start, end = line_range
            else:
                start, end = _top_level_block(lines, line)
            if (start, end) in seen_ranges:
                return result
            seen_ranges.add((start, end))
            block_text = "\n".join(lines[start - 1 : end])
            result.recovered.append(
                ast.Unparsed(
                    text=redact(block_text),
                    reason=str(error) or "SyntaxError",
                    span=span(start, end),
                )
            )
            result.issues.append(
                ast.Issue(
                    "error",
                    f"{tool}.syntax",
                    f"Unparseable Python block: {error.msg}.",
                    span(start, end),
                )
            )
            if line_range is not None and (start, end) == line_range:
                indent = " " * (len(lines[start - 1]) - len(lines[start - 1].lstrip()))
                lines[start - 1] = f"{indent}pass"
                for index in range(start, end):
                    lines[index] = ""
            else:
                for index in range(start - 1, end):
                    lines[index] = ""
            current = "\n".join(lines)
        except (ValueError, RecursionError) as error:
            result.issues.append(
                ast.Issue("error", f"{tool}.syntax", f"Could not parse Python: {error}")
            )
            return result
    result.issues.append(
        ast.Issue(
            "error",
            f"{tool}.syntax",
            f"Could not recover file after {MAX_ROUNDS} recovery rounds.",
        )
    )
    return result


__all__ = ["RecoveredModule", "parse_python_recovering"]
