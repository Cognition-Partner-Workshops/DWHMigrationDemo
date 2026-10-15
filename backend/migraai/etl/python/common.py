"""Shared helpers for the ETL parser packages.

The core lacks an ETL result/builder model, so these live in this package
(core change requests are tracked in docs). All ETL tools share the same
conventions: decode bytes, redact secrets, normalise paths, and build
ParsedObject entries whose nodes are ``ast.DialectStatement`` trees.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from migraai.discovery import ast
from migraai.discovery.objects import ParsedObject
from migraai.discovery.references import Reference


@dataclass(slots=True)
class EmbeddedSql:
    owner_qualified_name: str
    text: str
    location: str
    dialect_hint: str | None = None


@dataclass(slots=True)
class EtlParseResult:
    objects: list[ParsedObject] = field(default_factory=list)
    embedded_sql: list[EmbeddedSql] = field(default_factory=list)
    issues: list[ast.Issue] = field(default_factory=list)


def decode(data: bytes) -> str:
    text = data.decode("utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


_QUOTE_PAIRS = {"[": "]", '"': '"', "`": "`"}


def _unquote(part: str) -> ast.Identifier:
    if len(part) >= 2 and _QUOTE_PAIRS.get(part[0]) == part[-1]:
        return ast.Identifier(value=part[1:-1], quoted=True)
    return ast.Identifier(value=part)


def qname(text: str, *, split: bool = True) -> ast.QualifiedName:
    if split:
        parts = [_unquote(part) for part in text.split(".") if part != ""]
    else:
        parts = [_unquote(text.strip())]
    return ast.QualifiedName(parts=parts)


def span(
    start_line: int,
    end_line: int | None = None,
    start_col: int = 1,
    end_col: int = 1,
) -> ast.Span:
    return ast.Span(start_line, start_col, end_line or start_line, end_col)


_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    # connection strings: password=… / pwd=… (also env-style *PASSWORD*= / *_PWD= values)
    (
        re.compile(
            r"(?i)(\b[\w]*(?:password|_pwd|pwd|secret|token|api_key)\b\s*[=:]\s*)"
            r"[^\s'\"\};,]+(;[^\s'\"\};,]+)*"
        ),
        "\\1****",
    ),
    (re.compile(r"(?i)(--password\s*=\s*)\S+"), "\\1****"),
    (re.compile(r"(?i)(--password\s+)\S+"), "\\1****"),
    (re.compile(r"(?i)(\s-pw\s+)\S+"), "\\1****"),
    # url userinfo: scheme://user:secret@host
    (re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:)[^@\s/]+@"), "\\1****@"),
    # .LOGON tdpid/user,secret
    (re.compile(r"(?i)(\.LOGON\s+[^\s/,]+/[^\s,]+,)\S+"), "\\1****"),
    # db2 connect … using secret
    (re.compile(r"(?i)(db2\s+connect\b[^\n]*?\busing\s+)\S+"), "\\1****"),
    # sqlplus-style user/secret@db
    (re.compile(r"(\b[\w$#]+/)[^\s'\"/@;]+(@[\w.\-]+)"), "\\1****\\2"),
    # sqlplus bare user/secret argument (keep an @service suffix)
    (re.compile(r"(?i)(\bsqlplus\b[^\n]*?\s[\w$#]+/)[^\s'\"@;]+"), "\\1****"),
    # bcp -P, nzsql/nzload -w, beeline/hive/mysql -p
    (re.compile(r"(?i)(\b(?:bcp|sqlcmd)\b[^\n]*?-P\s+)\S+"), "\\1****"),
    (re.compile(r"(?i)(\b(?:nzsql|nzload)\b[^\n]*?-w\s+)\S+"), "\\1****"),
    (re.compile(r"(?i)(\b(?:beeline|hive|mysql)\b[^\n]*?-p\s+)\S+"), "\\1****"),
]


_REDACT_HINT = re.compile(
    r"(?i)passwor|pwd|secret|token|api_key|-pw|://|\.logon|\bdb2\b|sqlplus|\bbcp\b"
    r"|sqlcmd|nzsql|nzload|beeline|\bhive\b|\bmysql\b|[/@]"
)


def redact(text: str) -> str:
    if not _REDACT_HINT.search(text):
        return text
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


_VAR_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z_0-9]*)\}|\$([A-Za-z_][A-Za-z_0-9]*)|%%([A-Za-z_][A-Za-z_0-9]*)"
)


def normalize_path(path: str, rel_path: str, variables: dict[str, str] | None = None) -> str:
    """Normalise a script/package path reference per design §6."""
    path = path.strip().strip("'\"")
    rel_path = rel_path.replace("\\", "/")
    file_dir = posixpath.dirname(rel_path)
    variables = variables or {}

    anchored = bool(re.search(r"\$\(dirname\b[^)]*\)|\$\{0%/\*\}", path))
    path = re.sub(r"\$\(dirname\b[^)]*\)", file_dir, path)
    path = path.replace("${0%/*}", file_dir)

    def _sub(match: re.Match[str]) -> str:
        name = next(group for group in match.groups() if group is not None)
        return variables.get(name, match.group(0))

    path = _VAR_RE.sub(_sub, path)

    leading_var = re.match(r"^(?:\$[A-Za-z_][\w]*|\$\{[^}]+\}|%%[A-Za-z_][\w]*)/(.*)$", path)
    if leading_var:
        path = "/" + leading_var.group(1)
        path = posixpath.normpath(path)
        return path.lstrip("/")

    if path.startswith("/"):
        return posixpath.normpath(path)
    if path.startswith("~"):
        return path
    if anchored:
        # $(dirname $0)/${0%/*} already resolved relative to the file's dir
        return posixpath.normpath(path)
    if "/" in path and not path.startswith(("./", "../")):
        # design §6: a relative path with a directory component is pack-root relative
        return posixpath.normpath(path)
    base = file_dir or "."
    return posixpath.normpath(posixpath.join(base, path))


def file_unparsed(rel_path: str, reason: str, line: int = 1) -> ParsedObject:
    rel_path = rel_path.replace("\\", "/")
    node = ast.Unparsed(text="", reason=reason, span=span(line))
    return ParsedObject(
        type="file",
        schema=None,
        name=posixpath.basename(rel_path) or rel_path,
        qualified_name=rel_path,
        start_line=line,
        end_line=line,
        status="unparsed",
        references=[],
        issues=[
            ast.Issue(
                "error", "etl.unparsed_file", f"Could not parse {rel_path!r}: {reason}", span(line)
            )
        ],
        node=node,
    )


_OBJECT_STATEMENTS = re.compile(r"(?:^|\.)function$|(?:^|\.)task$")


def _collect_hints(node: ast.Node, hints: list[ast.RefHint], top: bool = True) -> None:
    """Gather RefHints from a node tree.

    Nested DialectStatements that represent child objects (e.g. a function
    inside a shell script, a task inside an airflow dag file) own their own
    references, so their subtrees are skipped for the enclosing object.
    """
    from dataclasses import fields

    def visit(value: Any) -> None:
        if isinstance(value, ast.DialectStatement) and value is not node:
            if _OBJECT_STATEMENTS.search(value.name):
                return
            for hint in value.references:
                hints.append(hint)
        if isinstance(value, ast.Node):
            for item in fields(value):
                if item.name in {"span", "references"}:
                    continue
                visit(getattr(value, item.name))
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    if isinstance(node, ast.DialectStatement):
        hints.extend(node.references)
    visit(node)


class XmlParseError(Exception):
    def __init__(self, message: str, line: int = 1) -> None:
        super().__init__(message)
        self.line = line


@dataclass
class XmlNode:
    tag: str
    attrib: dict[str, str]
    children: list[XmlNode]
    text: str
    line: int
    end_line: int

    @property
    def local(self) -> str:
        return self.tag.split(":", 1)[-1]

    def get(self, name: str, default: str | None = None) -> str | None:
        for key, value in self.attrib.items():
            if key.split(":", 1)[-1].casefold() == name.casefold():
                return value
        return default

    def find_all(self, local: str) -> list[XmlNode]:
        return [child for child in self.children if child.local.casefold() == local.casefold()]

    def find(self, local: str) -> XmlNode | None:
        for child in self.children:
            if child.local.casefold() == local.casefold():
                return child
        return None


def parse_xml(data: bytes) -> XmlNode:
    """Parse XML via defusedxml.sax (§3). Raises XmlParseError on malformed
    input, DTDs, entities and external references."""
    from xml.sax import handler
    from xml.sax.handler import feature_namespaces
    from xml.sax.xmlreader import AttributesImpl

    from defusedxml.sax import make_parser

    root_holder: list[XmlNode] = []
    stack: list[XmlNode] = []

    class _Handler(handler.ContentHandler):
        locator = None

        def setDocumentLocator(self, locator) -> None:  # noqa: N802
            self.locator = locator

        def startElement(self, name: str, attrs: AttributesImpl) -> None:
            node = XmlNode(
                tag=name,
                attrib={key: attrs.getValue(key) for key in attrs.getNames()},
                children=[],
                text="",
                line=self.locator.getLineNumber() if self.locator else 1,
                end_line=self.locator.getLineNumber() if self.locator else 1,
            )
            if stack:
                stack[-1].children.append(node)
            else:
                root_holder.append(node)
            stack.append(node)

        def characters(self, content: str) -> None:
            if stack:
                stack[-1].text += content
                if self.locator:
                    stack[-1].end_line = self.locator.getLineNumber()

        def endElement(self, name: str) -> None:
            node = stack.pop()
            if self.locator:
                node.end_line = self.locator.getLineNumber()

    sax_handler = _Handler()
    try:
        parser = make_parser()
        parser.setFeature(feature_namespaces, False)
        parser.setContentHandler(sax_handler)
        import io

        parser.parse(io.BytesIO(data))
    except Exception as error:
        line = 1
        sax_error = getattr(error, "_sax_parser", None) or error
        get_line = getattr(sax_error, "getLineNumber", None)
        if callable(get_line):
            try:
                line = get_line()
            except Exception:
                line = 1
        elif getattr(error, "getLineNumber", None):
            line = error.getLineNumber()
        raise XmlParseError(str(error), max(int(line or 1), 1)) from error
    if not root_holder:
        raise XmlParseError("Empty XML document.", 1)
    return root_holder[0]


class EtlBuilder:
    """Builds a consistent EtlParseResult for one file."""

    def __init__(self, tool: str, rel_path: str) -> None:
        self.tool = tool
        self.rel_path = rel_path.replace("\\", "/")
        self.objects: list[ParsedObject] = []
        self.embedded_sql: list[EmbeddedSql] = []
        self.issues: list[ast.Issue] = []

    def ref(
        self,
        name: str | ast.QualifiedName,
        ref_kind: str,
        span: ast.Span,
        *,
        split: bool = True,
    ) -> ast.RefHint:
        qualified = name if isinstance(name, ast.QualifiedName) else qname(name, split=split)
        return ast.RefHint(name=qualified, ref_kind=ref_kind, span=span)

    def add(
        self,
        type: str,
        qualified_name: str,
        node: ast.Node,
        start_line: int,
        end_line: int,
        issues: list[ast.Issue] | None = None,
    ) -> ParsedObject:
        issues = list(issues or [])
        hints: list[ast.RefHint] = []
        _collect_hints(node, hints)
        seen: set[tuple[str, str]] = set()
        references: list[Reference] = []
        for hint in hints:
            joined = ".".join(part.value for part in hint.name.parts)
            key = (joined, hint.ref_kind)
            if key in seen:
                continue
            seen.add(key)
            references.append(Reference(hint.name, hint.ref_kind, hint.span))
        schema, _, name = qualified_name.rpartition(".")
        node_issues = [
            issue
            for issue in issues
            if issue.span is None
            or start_line <= issue.span.start_line <= max(end_line, start_line)
        ]
        if isinstance(node, ast.Unparsed):
            status = "unparsed"
        elif any(isinstance(child, ast.Unparsed) for child in node.walk()) or any(
            issue.severity == "error" for issue in issues
        ):
            status = "partial"
        else:
            status = "parsed"
        obj = ParsedObject(
            type=type,
            schema=schema or None,
            name=name,
            qualified_name=qualified_name,
            start_line=start_line,
            end_line=end_line,
            status=status,
            references=references,
            issues=node_issues,
            node=node,
        )
        self.objects.append(obj)
        return obj

    def embed(self, owner: str, text: str, what: str, dialect_hint: str | None = None) -> None:
        text = redact(text)
        if not text.strip():
            return
        location = f"{self.rel_path}#{owner}:{what}"
        self.embedded_sql.append(
            EmbeddedSql(
                owner_qualified_name=owner,
                text=text,
                location=location,
                dialect_hint=dialect_hint,
            )
        )

    def issue(
        self, severity: str, code: str, message: str, span: ast.Span | None = None
    ) -> ast.Issue:
        issue = ast.Issue(severity, f"{self.tool}.{code}", message, span)
        self.issues.append(issue)
        return issue

    def result(self) -> EtlParseResult:
        return EtlParseResult(
            objects=list(self.objects),
            embedded_sql=list(self.embedded_sql),
            issues=list(self.issues),
        )


__all__ = [
    "EmbeddedSql",
    "EtlBuilder",
    "EtlParseResult",
    "decode",
    "file_unparsed",
    "normalize_path",
    "qname",
    "redact",
    "span",
]
