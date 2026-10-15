from migraai.validation.checks import dependency


def test_unresolved_and_out_of_order_dependencies(make_context, make_object) -> None:
    context = make_context(
        converted={"SRC.EARLIER", "SRC.LATER"},
        external={"SRC.EXTERNAL"},
        order=["SRC.OBJECT", "SRC.LATER"],
    )
    obj = make_object(
        source_name="SRC.OBJECT",
        dependencies=["SRC.MISSING", "SRC.LATER", "SRC.EXTERNAL"],
    )

    findings = dependency.validate(obj, context)

    assert [finding.rule_id for finding in findings] == [
        "dependency.unresolved",
        "dependency.order",
    ]
    assert all(finding.severity == "error" for finding in findings)


def test_earlier_converted_and_external_dependencies_pass(make_context, make_object) -> None:
    context = make_context(
        converted={"SRC.OBJECT", "SRC.PARENT"},
        external={"SRC.EXTERNAL"},
        order=["SRC.PARENT", "SRC.OBJECT"],
    )
    obj = make_object(
        source_name="SRC.OBJECT",
        dependencies=["SRC.PARENT", "SRC.EXTERNAL"],
    )

    assert dependency.validate(obj, context) == []
