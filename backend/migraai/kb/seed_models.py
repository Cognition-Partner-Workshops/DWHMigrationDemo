from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PlatformId = Literal[
    "teradata",
    "oracle",
    "sqlserver",
    "snowflake",
    "synapse",
    "db2",
    "hive",
    "netezza",
    "databricks",
    "redshift",
    "fabric",
    "bigquery",
    "clickhouse",
]
SourceId = Literal[
    "teradata", "oracle", "sqlserver", "snowflake", "synapse", "db2", "hive", "netezza"
]
TargetId = Literal[
    "databricks", "redshift", "snowflake", "fabric", "bigquery", "synapse", "clickhouse"
]
CanonicalType = Literal[
    "int8",
    "int16",
    "int32",
    "int64",
    "decimal",
    "float32",
    "float64",
    "boolean",
    "char",
    "varchar",
    "string",
    "binary",
    "varbinary",
    "date",
    "time",
    "time_tz",
    "timestamp",
    "timestamp_tz",
    "interval",
    "json",
    "array",
    "map",
    "struct",
    "geography",
    "geometry",
    "uuid",
    "xml",
    "period",
    "other",
]
Confidence = Literal["high", "medium", "low"]


class SeedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(SeedModel):
    text: str = Field(min_length=1)
    url: str = Field(min_length=1)
    accessed: date
    applies_to: str | None = None
    confidence: Confidence

    @field_validator("accessed", mode="before")
    @classmethod
    def parse_accessed(cls, value: object) -> date:
        if isinstance(value, date):
            return value
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("accessed must be YYYY-MM-DD")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("accessed must be YYYY-MM-DD") from error
        if parsed.isoformat() != value:
            raise ValueError("accessed must be YYYY-MM-DD")
        return parsed

    @field_validator("accessed")
    @classmethod
    def reject_future_date(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("accessed cannot be in the future")
        return value


class SourcePage(SeedModel):
    url: str = Field(min_length=1)
    path_prefix: str = Field(min_length=1)
    topic: str = Field(min_length=1)


class SourceSeeds(SeedModel):
    platform: PlatformId
    allowed_hosts: list[str] = Field(min_length=1)
    seeds: list[SourcePage]


class PlatformFacets(SeedModel):
    architecture: list[Claim]
    storage: list[Claim]
    compute: list[Claim]
    security: list[Claim]
    scalability: list[Claim]
    sql_dialect: list[Claim]
    etl_tooling: list[Claim]
    data_movement: list[Claim]


class PlatformProfile(SeedModel):
    id: PlatformId
    name: str = Field(min_length=1)
    roles: list[Literal["source", "target"]]
    versions_covered: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    facets: PlatformFacets
    unsupported_features: list[Claim]


class CatalogRow(SeedModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    object_types: list[str] = Field(min_length=1)
    extraction_hint: str = Field(min_length=1)


class ResourceCatalog(SeedModel):
    source: SourceId
    mandatory: list[CatalogRow]
    optional: list[CatalogRow]


class TypeMapping(SeedModel):
    native: str = Field(min_length=1)
    canonical: CanonicalType
    notes: str
    claim: Claim


class FunctionMapping(SeedModel):
    native: str = Field(min_length=1)
    canonical: str = Field(min_length=1)
    notes: str
    claim: Claim


class PlatformMappings(SeedModel):
    platform: PlatformId
    types: list[TypeMapping]
    functions: list[FunctionMapping]


class MajorDifferences(SeedModel):
    storage: list[Claim]
    compute: list[Claim]
    security: list[Claim]
    scalability: list[Claim]


class ObjectMapping(SeedModel):
    source_object: str = Field(min_length=1)
    target_object: str = Field(min_length=1)
    notes: str
    claim: Claim


class UnsupportedFeature(SeedModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    claim: Claim


class Workaround(SeedModel):
    for_id: str = Field(alias="for", min_length=1)
    claim: Claim


class Risk(SeedModel):
    level: str = Field(min_length=1)
    notes: str = Field(min_length=1)


class RiskMatrix(SeedModel):
    sql: Risk
    data: Risk
    security: Risk
    governance: Risk
    platform: Risk
    operational: Risk


class PairNotes(SeedModel):
    source: SourceId
    target: TargetId
    architecture_overview: list[Claim]
    major_differences: MajorDifferences
    recommended_strategy: str = Field(min_length=1)
    object_mapping: list[ObjectMapping]
    sql_dialect_differences: list[Claim]
    data_movement: list[Claim]
    etl_migration: list[Claim]
    security_migration: list[Claim]
    unsupported_features: list[UnsupportedFeature]
    workarounds: list[Workaround]
    risks: RiskMatrix
    optimization_hints: list[Claim]
