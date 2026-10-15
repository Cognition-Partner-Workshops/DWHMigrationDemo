import re


def identifier_parts(name: str) -> tuple[str, ...]:
    parts: list[str] = []
    part: list[str] = []
    quote_end: str | None = None
    index = 0
    while index < len(name):
        char = name[index]
        if quote_end is not None:
            if char == quote_end:
                if index + 1 < len(name) and name[index + 1] == quote_end:
                    part.append(char)
                    index += 1
                else:
                    quote_end = None
            else:
                part.append(char)
        elif char in {'"', "`"}:
            quote_end = char
        elif char == "[":
            quote_end = "]"
        elif char == ".":
            parts.append("".join(part).strip())
            part.clear()
        else:
            part.append(char)
        index += 1
    parts.append("".join(part).strip())
    return tuple(item for item in parts if item)


def normalize_name(name: str) -> str:
    return ".".join(part.casefold() for part in identifier_parts(name))


def _identifier_pattern(part: str) -> str:
    escaped = re.escape(part)
    return rf'(?:{escaped}|"{escaped}"|`{escaped}`|\[{escaped}\])'


def name_pattern(name: str) -> str:
    parts = identifier_parts(name)[-2:]
    qualified = r"\s*\.\s*".join(_identifier_pattern(part) for part in parts)
    return rf"(?<![\w$]){qualified}(?![\w$])"


def find_name(name: str, text: str) -> re.Match[str] | None:
    if not identifier_parts(name):
        return None
    return re.search(name_pattern(name), text, re.IGNORECASE)
