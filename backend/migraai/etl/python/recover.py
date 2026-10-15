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


def parse_python_recovering(text: str, tool: str) -> RecoveredModule:
    result = RecoveredModule(tree=None)
    current = text
    for _ in range(MAX_ROUNDS):
        try:
            result.tree = pyast.parse(current)
            return result
        except SyntaxError as error:
            line = error.lineno or 1
            lines = current.split("\n")
            start, end = _top_level_block(lines, line)
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
