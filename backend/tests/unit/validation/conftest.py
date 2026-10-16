import pytest

from migraai.platforms.pack import load_pack
from migraai.validation.context import ObjectUnderTest, OutputFile, ValidationContext


@pytest.fixture
def make_context():
    pack = load_pack("_example")

    def factory(**overrides):
        values = {
            "target": "databricks",
            "pack": pack,
            "name_map": {},
            "converted": set(),
            "external": set(),
            "order": [],
            "nameable_dependencies": set(),
            "table_columns": {},
            "type_map": {},
            "source_grants": {},
        }
        values.update(overrides)
        if "nameable_dependencies" not in overrides:
            values["nameable_dependencies"] = set(values["name_map"])
        return ValidationContext(**values)

    return factory


@pytest.fixture
def make_object():
    def factory(
        content: str = "SELECT 1",
        *,
        language: str = "sql",
        rel_path: str = "output.sql",
        object_type: str = "view",
        source_name: str = "SRC.SCHEMA.OBJECT",
        dependencies: list[str] | None = None,
        source_column_types: list[tuple[str, str]] | None = None,
        files: list[OutputFile] | None = None,
        etl_format_id: str | None = None,
    ) -> ObjectUnderTest:
        if files is None:
            files = [OutputFile(rel_path, language, content, etl_format_id)]
        return ObjectUnderTest(
            source_qualified_name=source_name,
            object_type=object_type,
            dependencies=dependencies or [],
            source_column_types=source_column_types or [],
            files=files,
        )

    return factory
