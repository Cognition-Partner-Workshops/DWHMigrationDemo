from migraai.validation.checks import object_reference


def test_stale_source_name_and_unreferenced_mapping_are_reported(make_context, make_object) -> None:
    context = make_context(name_map={"SRC.SCHEMA.ACCOUNT": "TGT.SILVER.ACCOUNT"})
    obj = make_object(
        content="SELECT id FROM src.schema.account",
        dependencies=["src.schema.account"],
    )

    findings = object_reference.validate(obj, context)

    assert {finding.rule_id for finding in findings} == {
        "object_reference.stale_name",
        "object_reference.unreferenced",
    }
    stale = next(finding for finding in findings if finding.rule_id.endswith("stale_name"))
    assert stale.severity == "error"


def test_mapped_quoted_name_is_referenced(make_context, make_object) -> None:
    context = make_context(name_map={"SRC.SCHEMA.ACCOUNT": "TGT.SILVER.ACCOUNT"})
    obj = make_object(
        content='SELECT id FROM "silver"."account"',
        dependencies=["SRC.SCHEMA.ACCOUNT"],
    )

    assert object_reference.validate(obj, context) == []
