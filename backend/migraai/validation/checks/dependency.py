from migraai.validation.context import ObjectUnderTest, ValidationContext
from migraai.validation.findings import Finding


def _normalized_names(names: set[str] | list[str]) -> set[str]:
    return {name.strip().casefold() for name in names}


def validate(obj: ObjectUnderTest, ctx: ValidationContext) -> list[Finding]:
    converted = _normalized_names(ctx.converted)
    external = _normalized_names(ctx.external)
    order: dict[str, int] = {}
    for index, name in enumerate(ctx.order):
        order.setdefault(name.strip().casefold(), index)
    object_name = obj.source_qualified_name.strip().casefold()
    findings: list[Finding] = []

    for dependency_name in obj.dependencies:
        normalized = dependency_name.strip().casefold()
        if normalized not in converted and normalized not in external:
            findings.append(
                Finding(
                    check="dependency",
                    rule_id="dependency.unresolved",
                    severity="error",
                    message=f"Dependency {dependency_name} was not converted or marked external.",
                )
            )
            continue
        if normalized in converted and (
            normalized not in order
            or object_name not in order
            or order[normalized] >= order[object_name]
        ):
            findings.append(
                Finding(
                    check="dependency",
                    rule_id="dependency.order",
                    severity="error",
                    message=f"Converted dependency {dependency_name} does not precede the object.",
                )
            )
    return findings
