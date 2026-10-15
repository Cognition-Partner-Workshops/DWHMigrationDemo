"""Small dialect-aware SQL lexer and Snowflake task graph checks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from migraai.etl.outputs.graph import check_graph
from migraai.validation.findings import Finding, Severity


@dataclass(frozen=True, slots=True)
class _Dialect:
    backslash_strings: bool = False
    backticks: bool = False
    brackets: bool = False
    dollar_quote: bool = False
    tagged_dollar_quote: bool = False
    triple_quotes: bool = False
    double_quote_strings: bool = False


_DIALECTS = {
    "redshift": _Dialect(dollar_quote=True, tagged_dollar_quote=True),
    "snowflake": _Dialect(backslash_strings=True, dollar_quote=True),
    "synapse": _Dialect(brackets=True),
    "bigquery": _Dialect(
        backslash_strings=True,
        backticks=True,
        triple_quotes=True,
        double_quote_strings=True,
    ),
    "clickhouse": _Dialect(backslash_strings=True, backticks=True),
}


@dataclass(frozen=True, slots=True)
class _LexResult:
    masked: str
    findings: list[Finding]
    has_tokens: bool


def _finding(
    format_id: str,
    name: str,
    message: str,
    line: int | None = None,
    severity: Severity = "error",
) -> Finding:
    return Finding("syntax", f"etl.{format_id}.{name}", severity, message, line)


def _mask_region(chars: list[str], source: str, start: int, end: int) -> None:
    for index in range(start, end):
        if source[index] != "\n":
            chars[index] = " "


def _line_at(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _lex_sql(content: str, format_id: str, dialect: _Dialect) -> _LexResult:
    masked = list(content)
    paren_stack: list[int] = []
    findings: list[Finding] = []
    has_tokens = False
    index = 0
    size = len(content)

    while index < size:
        char = content[index]
        next_two = content[index : index + 2]
        if char.isspace():
            index += 1
            continue
        if next_two == "--":
            end = content.find("\n", index)
            if end == -1:
                end = size
            _mask_region(masked, content, index, end)
            index = end
            continue
        if next_two == "/*":
            end_marker = content.find("*/", index + 2)
            if end_marker == -1:
                _mask_region(masked, content, index, size)
                findings.append(
                    _finding(
                        format_id,
                        "unterminated_comment",
                        "Unterminated block comment.",
                        _line_at(content, index),
                    )
                )
                index = size
                break
            end = end_marker + 2
            _mask_region(masked, content, index, end)
            index = end
            continue

        delimiter: str | None = None
        token_kind: str | None = None
        if dialect.triple_quotes and content[index : index + 3] in {"'''", '"""'}:
            delimiter = content[index : index + 3]
            token_kind = "string"
        elif char == "'":
            delimiter = "'"
            token_kind = "string"
        elif char == '"':
            delimiter = '"'
            token_kind = "string" if dialect.double_quote_strings else "identifier"
        elif char == "`" and dialect.backticks:
            delimiter = "`"
            token_kind = "identifier"
        elif char == "[" and dialect.brackets:
            delimiter = "]"
            token_kind = "identifier"
        elif (
            char == "$"
            and dialect.dollar_quote
            and (index == 0 or not (content[index - 1].isalnum() or content[index - 1] == "_"))
        ):
            dollar_match = re.match(
                r"\$(?:[A-Za-z_]\w*)?\$" if dialect.tagged_dollar_quote else r"\$\$",
                content[index:],
            )
            if dollar_match:
                delimiter = dollar_match.group()
                token_kind = "string"

        if delimiter is not None and token_kind is not None:
            has_tokens = True
            start = index
            closed = False
            if delimiter == "]":
                cursor = index + 1
                while cursor < size:
                    if content[cursor] == "]":
                        if cursor + 1 < size and content[cursor + 1] == "]":
                            cursor += 2
                            continue
                        closed = True
                        cursor += 1
                        break
                    cursor += 1
            elif delimiter in {"'", '"', "`"}:
                cursor = index + 1
                while cursor < size:
                    if char == "'" and dialect.backslash_strings and content[cursor] == "\\":
                        cursor += 2
                        continue
                    if content[cursor] == delimiter:
                        if cursor + 1 < size and content[cursor + 1] == delimiter:
                            cursor += 2
                            continue
                        closed = True
                        cursor += 1
                        break
                    cursor += 1
            elif len(delimiter) == 3:
                cursor = index + 3
                while cursor < size:
                    if content[cursor] == "\\" and dialect.backslash_strings:
                        cursor += 2
                        continue
                    if content.startswith(delimiter, cursor):
                        closed = True
                        cursor += 3
                        break
                    cursor += 1
            else:
                cursor = index + len(delimiter)
                close = content.find(delimiter, cursor)
                closed = close != -1
                cursor = size if close == -1 else close + len(delimiter)
            if not closed:
                _mask_region(masked, content, start, size)
                findings.append(
                    _finding(
                        format_id,
                        (
                            "unterminated_string"
                            if token_kind == "string"
                            else "unterminated_identifier"
                        ),
                        f"Unterminated SQL {token_kind}.",
                        _line_at(content, start),
                    )
                )
                index = size
                break
            if token_kind == "string":
                _mask_region(masked, content, start, cursor)
            else:
                for position in range(start, cursor):
                    # Identifiers remain visible to task-name parsing but do not contain parens.
                    if content[position] in "()":
                        masked[position] = " "
            index = cursor
            continue

        has_tokens = True
        if char == "(":
            paren_stack.append(index)
        elif char == ")":
            if paren_stack:
                paren_stack.pop()
            else:
                findings.append(
                    _finding(
                        format_id,
                        "unbalanced_parentheses",
                        "Closing parenthesis has no matching opening parenthesis.",
                        _line_at(content, index),
                    )
                )
        index += 1

    if paren_stack:
        findings.append(
            _finding(
                format_id,
                "unbalanced_parentheses",
                "Opening parenthesis is not closed.",
                _line_at(content, paren_stack[0]),
            )
        )
    if not has_tokens:
        findings.append(_finding(format_id, "empty", "SQL contains no tokens."))
    findings.sort(key=lambda item: (item.line is None, item.line if item.line is not None else 0))
    return _LexResult("".join(masked), findings, has_tokens)


def masked_sql(content: str, target: str) -> str:
    """Return SQL with strings and comments blanked while preserving line positions."""
    dialect = _DIALECTS[target]
    format_id = f"{target}.sql"
    return _lex_sql(content, format_id, dialect).masked


def _normalise_task_name(name: str) -> str:
    last = name.split(".")[-1]
    if last.startswith('"') and last.endswith('"'):
        return last[1:-1]
    return last.upper()


_CREATE_TASK = re.compile(
    r"\bCREATE\s+(?:(?:OR\s+(?:REPLACE|ALTER))\s+)?TASK\s+"
    r"""(?:IF\s+NOT\s+EXISTS\s+)?([\w$."]+)""",
    re.IGNORECASE,
)
_ALTER_TASK = re.compile(
    r"""\bALTER\s+TASK\s+(?:IF\s+EXISTS\s+)?([\w$."]+)""",
    re.IGNORECASE,
)
_AFTER_LIST = r"""([\w$."]+(?:\s*,\s*[\w$."]+)*)"""


def _task_line(content: str, index: int) -> int:
    return _line_at(content, index)


def _task_checks(format_id: str, content: str, lexed: _LexResult) -> list[Finding]:
    if any(item.severity == "error" for item in lexed.findings):
        return lexed.findings
    text = lexed.masked
    creates = list(_CREATE_TASK.finditer(text))
    findings: list[Finding] = []
    if not creates:
        return [_finding(format_id, "no_task", "No CREATE TASK statement was found.")]

    tasks: list[dict[str, object]] = []
    for index, match in enumerate(creates):
        boundary = creates[index + 1].start() if index + 1 < len(creates) else len(text)
        statement_end = text.find(";", match.start(), boundary)
        if statement_end == -1:
            statement_end = boundary
        segment = text[match.start() : statement_end]
        as_match: re.Match[str] | None = None
        for candidate in re.finditer(r"\bAS\b", segment, re.IGNORECASE):
            preceding = segment[: candidate.start()]
            following = segment[candidate.end() :]
            if re.search(r"\bEXECUTE\s*$", preceding, re.IGNORECASE) and re.match(
                r"\s+(?:USER|OWNER)\b", following, re.IGNORECASE
            ):
                continue
            as_match = candidate
            break
        header_end = as_match.start() if as_match else len(segment)
        header = segment[:header_end]
        dependencies: list[str] = []
        after_match = re.search(rf"\bAFTER\s+{_AFTER_LIST}", header, re.IGNORECASE)
        if after_match:
            dependencies = [
                _normalise_task_name(part.strip()) for part in after_match.group(1).split(",")
            ]
        schedule = re.search(r"\bSCHEDULE\s*=", header, re.IGNORECASE) is not None
        line = _task_line(content, match.start())
        task = {
            "name": _normalise_task_name(match.group(1)),
            "line": line,
            "dependencies": dependencies,
            "schedule": schedule,
            "has_body": False,
        }
        if as_match:
            body = segment[as_match.end() :]
            task["has_body"] = bool(body.strip().strip(";").strip())
        tasks.append(task)
        if not task["has_body"]:
            findings.append(
                _finding(
                    format_id,
                    "missing_body",
                    f"Task {match.group(1)!r} has no SQL body after AS.",
                    line,
                )
            )

    alter_matches = list(_ALTER_TASK.finditer(text))
    for index, match in enumerate(alter_matches):
        boundary_candidates = [
            candidate
            for candidate in (
                text.find(";", match.start()),
                alter_matches[index + 1].start() if index + 1 < len(alter_matches) else -1,
                _CREATE_TASK.search(text, match.end()).start()
                if _CREATE_TASK.search(text, match.end())
                else -1,
            )
            if candidate >= 0
        ]
        end = min(boundary_candidates) if boundary_candidates else len(text)
        statement = text[match.start() : end]
        after_match = re.search(rf"\bADD\s+AFTER\s+{_AFTER_LIST}", statement, re.IGNORECASE)
        if not after_match:
            continue
        name = _normalise_task_name(match.group(1))
        dependencies = [
            _normalise_task_name(part.strip()) for part in after_match.group(1).split(",")
        ]
        for task in tasks:
            if task["name"] == name:
                task["dependencies"] = [*task["dependencies"], *dependencies]

    known_names = {str(task["name"]) for task in tasks}
    for task in tasks:
        dependencies = task["dependencies"]
        if dependencies and task["schedule"]:
            findings.append(
                _finding(
                    format_id,
                    "schedule_on_child",
                    f"Task {task['name']!r} has both SCHEDULE and AFTER.",
                    task["line"],
                )
            )
        if not dependencies and not task["schedule"]:
            findings.append(
                _finding(
                    format_id,
                    "root_without_schedule",
                    f"Root task {task['name']!r} has no SCHEDULE.",
                    task["line"],
                    severity="warning",
                )
            )
        for dependency in dependencies:
            if dependency not in known_names:
                findings.append(
                    _finding(
                        format_id,
                        "unknown_predecessor",
                        f"Task {task['name']!r} depends on unknown task {dependency!r}.",
                        task["line"],
                    )
                )

    graph = check_graph(
        [(str(task["name"]), task["dependencies"], int(task["line"])) for task in tasks]
    )
    findings.extend(
        _finding(format_id, "duplicate_task", f"Duplicate task {name!r}.", line)
        for name, line in graph.duplicates
    )
    findings.extend(
        _finding(
            format_id,
            "dependency_cycle",
            f"Task dependency cycle: {' -> '.join(cycle)}.",
            line,
        )
        for cycle, line in graph.cycles
    )
    findings.sort(key=lambda item: (item.line is None, item.line if item.line is not None else 0))
    return findings


def check_sql_output(format_id: str, content: str) -> list[Finding]:
    target = format_id.split(".", 1)[0]
    dialect = _DIALECTS[target]
    lexed = _lex_sql(content, format_id, dialect)
    if format_id == "snowflake.tasks_sql":
        return _task_checks(format_id, content, lexed)
    return lexed.findings
