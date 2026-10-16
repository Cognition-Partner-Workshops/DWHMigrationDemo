import re

_SQL_NON_CODE = re.compile(r"(?s)--[^\r\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"")
_SNOWFLAKE_STAGE_COPY = re.compile(
    r"\bCOPY\s+INTO\b[^;]*?\bFROM\s+@[^\s,;)]*",
    re.IGNORECASE | re.DOTALL,
)
_STATEMENT_START = re.compile(
    r"(?m)^[ \t]*(?:WITH|SELECT|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|"
    r"TRUNCATE|CALL|EXEC(?:UTE)?|GRANT|REVOKE|COPY|BEGIN|DECLARE|SET|USE)\b",
    re.IGNORECASE,
)


def _code_only(sql: str) -> str:
    def blank(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group())

    return _SQL_NON_CODE.sub(blank, sql)


def has_snowflake_stage_copy(sql: str, target: str) -> bool:
    if target.casefold() == "snowflake":
        return False
    return _SNOWFLAKE_STAGE_COPY.search(_code_only(sql)) is not None


def split_snowflake_stage_copy_statements(sql: str) -> tuple[str, list[str]]:
    code = _code_only(sql)
    ranges: list[tuple[int, int]] = []
    for match in _SNOWFLAKE_STAGE_COPY.finditer(code):
        next_statement = _STATEMENT_START.search(code, match.end())
        semicolon = code.find(";", match.end())
        if next_statement is not None and (semicolon < 0 or next_statement.start() < semicolon):
            end = next_statement.start()
        elif semicolon >= 0:
            end = semicolon + 1
        elif next_statement is not None:
            end = next_statement.start()
        else:
            end = len(code)
        ranges.append((match.start(), end))

    stage_copies = [sql[start:end].strip() for start, end in ranges]
    for start, end in reversed(ranges):
        blanked = "".join("\n" if char == "\n" else " " for char in sql[start:end])
        sql = f"{sql[:start]}{blanked}{sql[end:]}"
    return sql, stage_copies


def exclude_snowflake_stage_copy_statements(sql: str) -> str:
    return split_snowflake_stage_copy_statements(sql)[0]
