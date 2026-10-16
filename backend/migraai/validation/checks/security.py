import re

import sqlglot
from sqlglot import ErrorLevel, exp
from sqlglot.errors import ParseError

from migraai.core.secret_mask import (
    _SECRET_PATTERNS,
    MASKED_SECRET_PLACEHOLDER,
    blank_masked_secret_placeholders,
)
from migraai.platforms.pack import _strip_comments, evaluate_rules
from migraai.validation.context import ObjectUnderTest, OutputFile, ValidationContext
from migraai.validation.dialects import sqlglot_dialect
from migraai.validation.findings import Finding
from migraai.validation.names import identifier_parts, normalize_name


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _secret_findings(file: OutputFile) -> list[Finding]:
    findings: list[Finding] = []
    scanned_content = blank_masked_secret_placeholders(file.content)
    for pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(scanned_content):
            findings.append(
                Finding(
                    check="security",
                    rule_id="security.secret_in_output",
                    severity="error",
                    message=f"Potential secret found in {file.rel_path}.",
                    line=_line(file.content, match.start()),
                )
            )
    return findings


def _grant_value(expression: exp.Expression) -> str:
    value = getattr(expression, "this", expression)
    if isinstance(value, exp.Expression):
        return value.name or value.sql()
    return str(value)


def _grant_line(expression: exp.Expression) -> int | None:
    if "line" in expression.meta:
        return expression.meta["line"]
    value = getattr(expression, "this", None)
    if isinstance(value, exp.Expression):
        line = value.meta.get("line")
        return line if isinstance(line, int) else None
    return None


def _ast_grants(file: OutputFile, ctx: ValidationContext) -> list[tuple[str, str, int | None]]:
    try:
        statements = sqlglot.parse(
            file.content,
            read=sqlglot_dialect(ctx.target),
            error_level=ErrorLevel.RAISE,
        )
    except ParseError:
        return []
    grants: list[tuple[str, str, int | None]] = []
    for statement in statements:
        if not isinstance(statement, exp.Grant):
            continue
        privileges = statement.args.get("privileges") or []
        principals = statement.args.get("principals") or []
        for privilege in privileges:
            privilege_name = _grant_value(privilege).upper()
            for principal in principals:
                grantee_name = _grant_value(principal).upper()
                grants.append((privilege_name, grantee_name, _grant_line(principal)))
    return grants


def _fallback_grants(file: OutputFile) -> list[tuple[str, str, int | None]]:
    text = _strip_comments(file.content, "sql")
    grants: list[tuple[str, str, int | None]] = []
    pattern = re.compile(
        r"\bGRANT\s+(?P<privileges>.*?)\s+ON\b.*?\s+TO\s+"
        r"(?P<grantees>[^;\r\n]+)",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        privileges = [
            re.sub(r"\s+", " ", item.strip()).upper()
            for item in match.group("privileges").split(",")
            if item.strip()
        ]
        grantees_text = re.sub(
            r"\s+WITH\s+GRANT\s+OPTION\s*$", "", match.group("grantees"), flags=re.IGNORECASE
        )
        grantees = [item.strip().strip('"`[]').upper() for item in grantees_text.split(",")]
        for privilege in privileges:
            for grantee in grantees:
                grants.append((privilege, grantee, _line(text, match.start())))
    return grants


def _normalized_grants(ctx: ValidationContext, obj: ObjectUnderTest) -> set[tuple[str, str]]:
    source = next(
        (
            grants
            for name, grants in ctx.source_grants.items()
            if normalize_name(name) == normalize_name(obj.source_qualified_name)
        ),
        set(),
    )
    return {
        (
            ".".join(identifier_parts(grantee)).upper(),
            " ".join(privilege.upper().split()),
        )
        for grantee, privilege in source
    }


def _grant_findings(
    file: OutputFile, obj: ObjectUnderTest, ctx: ValidationContext
) -> list[Finding]:
    grants = _ast_grants(file, ctx)
    if not grants:
        grants = _fallback_grants(file)
    source_grants = _normalized_grants(ctx, obj)
    findings: list[Finding] = []

    for privilege, grantee, line in grants:
        normalized_privilege = " ".join(privilege.upper().split())
        normalized_grantee = ".".join(identifier_parts(grantee)).upper()
        if normalized_privilege in {"ALL", "ALL PRIVILEGES"}:
            findings.append(
                Finding(
                    check="security",
                    rule_id="security.grant_all",
                    severity="error",
                    message=f"GRANT ALL is not permitted in {file.rel_path}.",
                    line=line,
                )
            )
        if normalized_grantee == "PUBLIC":
            findings.append(
                Finding(
                    check="security",
                    rule_id="security.public_grant",
                    severity="error",
                    message=f"Granting privileges to PUBLIC is not permitted in {file.rel_path}.",
                    line=line,
                )
            )
        if source_grants:
            if (normalized_grantee, normalized_privilege) not in source_grants:
                findings.append(
                    Finding(
                        check="security",
                        rule_id="security.privilege_widening",
                        severity="error",
                        message=(
                            f"Grant {normalized_privilege} to {normalized_grantee} "
                            "is not present in the source grants."
                        ),
                        line=line,
                    )
                )
        else:
            findings.append(
                Finding(
                    check="security",
                    rule_id="security.unverified_grant",
                    severity="warning",
                    message=f"Grant to {normalized_grantee} has no source grant to compare.",
                    line=line,
                )
            )
    return findings


def validate(obj: ObjectUnderTest, ctx: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    for file in obj.files:
        safe_content = blank_masked_secret_placeholders(file.content)
        findings.extend(
            evaluate_rules(
                ctx.pack,
                safe_content,
                language=file.language,
                object_type=obj.object_type.casefold(),
                checks={"security"},
            )
        )
        findings.extend(_secret_findings(file))
        placeholder_offset = file.content.find(MASKED_SECRET_PLACEHOLDER)
        if placeholder_offset >= 0:
            findings.append(
                Finding(
                    check="security",
                    rule_id="security.masked_secret_placeholder",
                    severity="warning",
                    message=(
                        "The source had a secret that was masked before sending. Replace the "
                        "placeholder with a secret reference (for example a SECRET object or key "
                        "vault) before deploying."
                    ),
                    line=_line(file.content, placeholder_offset),
                )
            )
        if file.language.casefold() == "sql":
            findings.extend(_grant_findings(file, obj, ctx))
    return findings
