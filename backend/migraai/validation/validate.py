from migraai.validation.checks import (
    dependency,
    lint,
    object_reference,
    rule,
    security,
    semantic,
    syntax,
)
from migraai.validation.context import ObjectUnderTest, ValidationContext
from migraai.validation.findings import CheckName, CheckResult, Finding
from migraai.validation.names import normalize_name

CHECK_ORDER: tuple[CheckName, ...] = (
    "syntax",
    "lint",
    "semantic",
    "rule",
    "dependency",
    "object_reference",
    "security",
)


def _has_mapped_dependency(obj: ObjectUnderTest, ctx: ValidationContext) -> bool:
    mapped_sources = {normalize_name(name) for name in ctx.name_map}
    return any(normalize_name(dependency) in mapped_sources for dependency in obj.dependencies)


def _applicable(obj: ObjectUnderTest, ctx: ValidationContext, check: CheckName) -> bool:
    if check == "syntax":
        return bool(obj.files)
    if check == "lint":
        return any(
            file.language.casefold() in rule.languages
            and (
                rule.object_types is None
                or obj.object_type.casefold() in {name.casefold() for name in rule.object_types}
            )
            for file in obj.files
            for rule in ctx.pack.rules
            if rule.check == "lint"
        )
    if check == "semantic":
        return any(file.language.casefold() == "sql" for file in obj.files)
    if check == "rule":
        return any(file.language.casefold() in {"sql", "python"} for file in obj.files) or any(
            file.language.casefold() in rule.languages
            and (
                rule.object_types is None
                or obj.object_type.casefold() in {name.casefold() for name in rule.object_types}
            )
            for file in obj.files
            for rule in ctx.pack.rules
            if rule.check == "rule"
        )
    if check == "dependency":
        return bool(obj.dependencies)
    if check == "object_reference":
        return _has_mapped_dependency(obj, ctx)
    if check == "security":
        return bool(obj.files)
    return False


def _findings_for(check: CheckName, obj: ObjectUnderTest, ctx: ValidationContext) -> list[Finding]:
    validator = {
        "syntax": syntax.validate,
        "lint": lint.validate,
        "semantic": semantic.validate,
        "rule": rule.validate,
        "dependency": dependency.validate,
        "object_reference": object_reference.validate,
        "security": security.validate,
    }[check]
    return validator(obj, ctx)


def validate_object(obj: ObjectUnderTest, ctx: ValidationContext) -> list[CheckResult]:
    results: list[CheckResult] = []
    for check in CHECK_ORDER:
        findings = _findings_for(check, obj, ctx)
        if not _applicable(obj, ctx, check):
            status = "not_applicable"
        elif any(finding.severity == "error" for finding in findings):
            status = "failed"
        else:
            status = "passed"
        results.append(CheckResult(check=check, status=status, findings=findings))
    return results


def pass_rate(results: list[CheckResult]) -> float:
    applicable = [result for result in results if result.status in {"passed", "failed"}]
    if not applicable:
        return 1.0
    passed = sum(result.status == "passed" for result in applicable)
    return passed / len(applicable)
