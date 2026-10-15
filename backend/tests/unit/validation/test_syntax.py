import sys
from types import ModuleType

import pytest

from migraai.etl.outputs import formats_for
from migraai.validation.checks import syntax
from migraai.validation.dialects import TARGET_DIALECTS, sqlglot_dialect
from migraai.validation.findings import Finding


@pytest.mark.parametrize(
    ("target", "dialect"),
    [
        ("databricks", "databricks"),
        ("snowflake", "snowflake"),
        ("synapse", "tsql"),
    ],
)
def test_valid_sql_parses_in_target_dialect(target, dialect, make_context, make_object) -> None:
    context = make_context(target=target)
    obj = make_object(content="SELECT 1 AS id")

    assert TARGET_DIALECTS[target] == dialect
    assert sqlglot_dialect(target) == dialect
    assert syntax.validate(obj, context) == []


def test_sql_parse_error_has_line_number(make_context, make_object) -> None:
    findings = syntax.validate(make_object(content="SELECT 1\nFROM"), make_context())

    assert findings[0].rule_id == "syntax.parse_error"
    assert findings[0].severity == "error"
    assert findings[0].line == 2


def test_opaque_sql_statement_is_a_warning(make_context, make_object) -> None:
    findings = syntax.validate(make_object(content="SET foo bar"), make_context())

    assert [(finding.rule_id, finding.severity) for finding in findings] == [
        ("syntax.opaque_statement", "warning")
    ]


def test_unknown_output_language_is_an_error(make_context, make_object) -> None:
    findings = syntax.validate(
        make_object(language="ruby", content="puts 1"),
        make_context(),
    )

    assert [finding.rule_id for finding in findings] == ["syntax.unsupported_language"]
    assert findings[0].severity == "error"


def test_python_json_and_yaml_are_parsed(make_context, make_object) -> None:
    context = make_context()
    good_files = [
        make_object(language="python", content="def transform(row):\n    return row"),
        make_object(language="json", content='{"task": {"name": "copy"}}'),
        make_object(language="yaml", content="task:\n  name: copy\n"),
    ]
    assert all(syntax.validate(obj, context) == [] for obj in good_files)

    bad_python = make_object(language="python", content="x = 1\nif:")
    bad_json = make_object(language="json", content='{"task":\n}')
    bad_yaml = make_object(language="yaml", content="task: [\n")

    for obj in (bad_python, bad_json, bad_yaml):
        findings = syntax.validate(obj, context)
        assert len(findings) == 1
        assert findings[0].rule_id == "syntax.parse_error"
        assert findings[0].severity == "error"
        assert findings[0].line is not None


def test_etl_output_validator_is_imported_lazily(monkeypatch, make_context, make_object) -> None:
    stub = ModuleType("migraai.etl.outputs")
    stub.validate_output = lambda format_id, content: [
        Finding("syntax", "etl.stub.invalid", "error", f"{format_id}: {content}")
    ]
    monkeypatch.setitem(sys.modules, "migraai.etl.outputs", stub)
    obj = make_object(content="{}", language="json", etl_format_id="databricks.workflows_job")

    findings = syntax.validate(obj, make_context())

    assert [finding.rule_id for finding in findings] == ["etl.stub.invalid"]
    assert findings[0].severity == "error"


def test_real_etl_output_validator_runs_for_tagged_file(make_context, make_object) -> None:
    output_format = next(
        fmt for fmt in formats_for("databricks") if fmt.id == "databricks.notebook"
    )
    content = output_format.skeleton.removeprefix("# Databricks notebook source\n")

    findings = syntax.validate(
        make_object(
            language="python",
            content=content,
            etl_format_id=output_format.id,
        ),
        make_context(target="databricks"),
    )

    assert "etl.databricks.notebook.missing_header" in {finding.rule_id for finding in findings}
