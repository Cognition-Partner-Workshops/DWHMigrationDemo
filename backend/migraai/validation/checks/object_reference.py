from migraai.platforms.pack import _strip_comments
from migraai.validation.context import ObjectUnderTest, ValidationContext
from migraai.validation.findings import Finding
from migraai.validation.names import find_name, identifier_parts, normalize_name


def validate(obj: ObjectUnderTest, ctx: ValidationContext) -> list[Finding]:
    mapped_names = {normalize_name(source): target for source, target in ctx.name_map.items()}
    output_files = [
        (file, _strip_comments(file.content, file.language.casefold())) for file in obj.files
    ]
    findings: list[Finding] = []

    for dependency_name in obj.dependencies:
        target_name = mapped_names.get(normalize_name(dependency_name))
        if target_name is None:
            continue
        target_parts = identifier_parts(target_name)
        source_parts = identifier_parts(dependency_name)
        target_matches = [
            (file, match)
            for file, text in output_files
            if (match := find_name(target_name, text)) is not None
        ]
        if not target_matches:
            findings.append(
                Finding(
                    check="object_reference",
                    rule_id="object_reference.unreferenced",
                    severity="warning",
                    message=f"Mapped dependency {target_name} is not referenced by the output.",
                )
            )
        if tuple(part.casefold() for part in source_parts[-2:]) != tuple(
            part.casefold() for part in target_parts[-2:]
        ):
            stale_matches = [
                (file, match)
                for file, text in output_files
                if (match := find_name(dependency_name, text)) is not None
            ]
            if stale_matches:
                file, match = stale_matches[0]
                findings.append(
                    Finding(
                        check="object_reference",
                        rule_id="object_reference.stale_name",
                        severity="error",
                        message=(
                            f"Output references source dependency {dependency_name} "
                            f"instead of {target_name}."
                        ),
                        line=file.content.count("\n", 0, match.start()) + 1,
                    )
                )
    return findings
