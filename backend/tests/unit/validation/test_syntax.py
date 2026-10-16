import sys
from pathlib import Path
from types import ModuleType

import pytest
import sqlglot

from migraai.etl.outputs import formats_for
from migraai.validation.checks import syntax
from migraai.validation.dialects import TARGET_DIALECTS, sqlglot_dialect
from migraai.validation.findings import Finding
from migraai.validation.sql import exclude_snowflake_stage_copy_statements
from migraai.validation.validate import validate_object


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


def test_tokenizer_error_is_reported_as_a_syntax_failure(make_context, make_object) -> None:
    pack = Path(__file__).resolve().parents[4] / "testpacks" / "netezza" / "routines.sql"
    obj = make_object(content=pack.read_text(encoding="utf-8"), rel_path="routines.sql")
    context = make_context(target="clickhouse")

    findings = syntax.validate(obj, context)

    assert findings[0].rule_id == "syntax.parse_error"
    assert findings[0].severity == "error"
    assert len(validate_object(obj, context)) == 7


def test_snowflake_sql_procedure_parse_error_is_a_syntax_failure(make_context, make_object) -> None:
    pack = Path(__file__).resolve().parents[4] / "testpacks" / "snowflake" / "40_pipelines.sql"
    content = pack.read_text(encoding="utf-8")
    start = content.index("CREATE PROCEDURE analytics.curated.p_refresh")
    end = content.index("$$;", start) + 3
    obj = make_object(content=content[start:end], rel_path="40_pipelines.sql")

    findings = syntax.validate(obj, make_context(target="databricks"))

    assert findings[0].rule_id == "syntax.parse_error"


def test_snowflake_stage_copy_is_rejected_before_target_parsing(
    monkeypatch, make_context, make_object
) -> None:
    def unexpected_parse(*args, **kwargs):
        pytest.fail("The non-terminating target parser must not receive a Snowflake stage COPY.")

    monkeypatch.setattr(syntax.sqlglot, "parse", unexpected_parse)
    obj = make_object(
        content="COPY INTO target FROM @my_stage/path/orders.csv;",
        rel_path="loads.sql",
    )

    findings = syntax.validate(obj, make_context(target="databricks"))

    assert findings[0].rule_id == "syntax.parse_error"
    assert "Snowflake stage paths" in findings[0].message


def test_snowflake_stage_copy_does_not_break_other_validators(make_context, make_object) -> None:
    source_path = Path(__file__).resolve().parents[4] / "testpacks/snowflake/90_loads.sql"
    source_text = source_path.read_text(encoding="utf-8")
    candidate_text = "\n".join(sqlglot.transpile(source_text, read="snowflake", write="databricks"))
    obj = make_object(content=candidate_text, rel_path="90_loads.sql")
    filtered_text = exclude_snowflake_stage_copy_statements(candidate_text)

    results = validate_object(obj, make_context(target="databricks"))

    assert "COPY INTO" not in filtered_text
    assert "MERGE INTO" in filtered_text
    assert len(results) == 7
    assert results[0].check == "syntax"
    assert results[0].status == "failed"


def test_native_snowflake_stage_copy_does_not_break_other_validators(
    make_context, make_object
) -> None:
    source_path = Path(__file__).resolve().parents[4] / "testpacks/snowflake/90_loads.sql"
    source_text = source_path.read_text(encoding="utf-8")
    obj = make_object(content=source_text, rel_path="90_loads.sql")

    results = validate_object(obj, make_context(target="snowflake"))

    assert len(results) == 7
    assert results[0].check == "syntax"


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
