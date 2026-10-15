import ast
import importlib
import json

import sqlglot
import yaml
from sqlglot import ErrorLevel, exp
from sqlglot.errors import ParseError

from migraai.validation.context import ObjectUnderTest, OutputFile, ValidationContext
from migraai.validation.dialects import sqlglot_dialect
from migraai.validation.findings import Finding


def _finding(
    file: OutputFile,
    rule_id: str,
    severity: str,
    message: str,
    line: int | None = None,
) -> Finding:
    return Finding(
        check="syntax",
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        message=f"{file.rel_path}: {message}",
        line=line,
    )


def _sql_findings(file: OutputFile, ctx: ValidationContext) -> list[Finding]:
    try:
        statements = sqlglot.parse(
            file.content,
            read=sqlglot_dialect(ctx.target),
            error_level=ErrorLevel.RAISE,
        )
    except ParseError as error:
        errors = getattr(error, "errors", [])
        line = errors[0].get("line") if errors else None
        return [
            _finding(
                file,
                "syntax.parse_error",
                "error",
                "SQL could not be parsed.",
                line,
            )
        ]

    return [
        _finding(
            file,
            "syntax.opaque_statement",
            "warning",
            "SQLGlot parsed this statement as an opaque command.",
            statement.meta.get("line"),
        )
        for statement in statements
        if isinstance(statement, exp.Command)
    ]


def _python_findings(file: OutputFile) -> list[Finding]:
    try:
        ast.parse(file.content, filename=file.rel_path)
    except SyntaxError as error:
        return [
            _finding(
                file,
                "syntax.parse_error",
                "error",
                "Python could not be parsed.",
                error.lineno,
            )
        ]
    return []


def _json_findings(file: OutputFile) -> list[Finding]:
    try:
        json.loads(file.content)
    except json.JSONDecodeError as error:
        return [
            _finding(
                file,
                "syntax.parse_error",
                "error",
                "JSON could not be parsed.",
                error.lineno,
            )
        ]
    return []


def _yaml_findings(file: OutputFile) -> list[Finding]:
    try:
        yaml.safe_load(file.content)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        return [
            _finding(
                file,
                "syntax.parse_error",
                "error",
                "YAML could not be parsed.",
                line,
            )
        ]
    return []


def _etl_findings(file: OutputFile) -> list[Finding]:
    if file.etl_format_id is None:
        return []
    try:
        outputs = importlib.import_module("migraai.etl.outputs")
        validate_output = outputs.validate_output
    except (ImportError, AttributeError):
        return []
    return validate_output(file.etl_format_id, file.content)


def validate(obj: ObjectUnderTest, ctx: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    parsers = {
        "sql": lambda file: _sql_findings(file, ctx),
        "python": _python_findings,
        "json": _json_findings,
        "yaml": _yaml_findings,
    }
    for file in obj.files:
        parser = parsers.get(file.language.casefold())
        if parser is None:
            findings.append(
                _finding(
                    file,
                    "syntax.unsupported_language",
                    "error",
                    f"Unsupported output language {file.language!r}.",
                )
            )
        else:
            findings.extend(parser(file))
        findings.extend(_etl_findings(file))
    return findings
