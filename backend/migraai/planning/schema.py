from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from migraai.planning.sections import SECTIONS


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kb_id: int | None = None
    url: str | None = None
    note: str
    unverified: bool = False

    @model_validator(mode="after")
    def one_reference(self) -> Citation:
        if (self.kb_id is None) == (self.url is None):
            raise ValueError("Citation must contain exactly one of kb_id or url.")
        return self


class PlanSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    content: str
    citations: list[Citation] = Field(default_factory=list)
    platform_level_estimate: bool
    manual_review: bool = False
    unverified_citations: bool = False

    @model_validator(mode="after")
    def platform_estimate_flag(self) -> PlanSection:
        if self.id in {"Q", "S", "T"} and not self.platform_level_estimate:
            raise ValueError("Sections Q, S, and T must be platform-level estimates.")
        return self


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    overall_confidence: float | None = Field(ge=0, le=1)
    sections: list[PlanSection]

    @model_validator(mode="after")
    def ordered_sections(self) -> Plan:
        actual = [(section.id, section.title) for section in self.sections]
        if actual != SECTIONS:
            raise ValueError("Plan must contain exactly sections A-U in the required order.")
        return self


def draft7_schema() -> dict[str, Any]:
    section_schemas = [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "title", "content", "citations", "platform_level_estimate"],
            "properties": {
                "id": {"type": "string", "const": section_id},
                "title": {"type": "string", "const": title},
                "content": {"type": "string"},
                "citations": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["kb_id", "note"],
                                "properties": {
                                    "kb_id": {"type": "integer"},
                                    "note": {"type": "string"},
                                    "unverified": {"type": "boolean"},
                                },
                            },
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["url", "note"],
                                "properties": {
                                    "url": {"type": "string"},
                                    "note": {"type": "string"},
                                    "unverified": {"type": "boolean"},
                                },
                            },
                        ]
                    },
                },
                "platform_level_estimate": {
                    "type": "boolean",
                    **({"const": True} if section_id in {"Q", "S", "T"} else {}),
                },
                "manual_review": {"type": "boolean"},
                "unverified_citations": {"type": "boolean"},
            },
        }
        for section_id, title in SECTIONS
    ]
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "required": ["source", "target", "overall_confidence", "sections"],
        "properties": {
            "source": {"type": "string"},
            "target": {"type": "string"},
            "overall_confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "sections": {
                "type": "array",
                "minItems": 21,
                "maxItems": 21,
                "items": section_schemas,
                "additionalItems": False,
            },
        },
    }


SCHEMA = draft7_schema()
