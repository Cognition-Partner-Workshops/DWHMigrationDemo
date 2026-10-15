from migraai.platforms.pack import evaluate_rules
from migraai.validation.context import ObjectUnderTest, ValidationContext
from migraai.validation.findings import Finding


def validate(obj: ObjectUnderTest, ctx: ValidationContext) -> list[Finding]:
    return [
        finding
        for file in obj.files
        for finding in evaluate_rules(
            ctx.pack,
            file.content,
            language=file.language,
            object_type=obj.object_type.casefold(),
            checks={"rule"},
        )
    ]
