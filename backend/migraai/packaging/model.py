"""Frozen snapshot of a finished conversion run, consumed by every Phase 5 generator.

Generators (workbook, execution scripts, session report, zipper) are pure functions of
``PackageInput``: they never read the job, the session store or the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from migraai.validation.findings import CheckName, Severity

ObjectStatus = Literal["converted", "expert_review", "failed", "cancelled"]
MappingKind = Literal["object", "data_type", "function", "syntax", "security"]


@dataclass(frozen=True, slots=True)
class FindingSummary:
    check: CheckName
    rule_id: str
    severity: Severity
    message: str
    line: int | None = None


@dataclass(frozen=True, slots=True)
class CheckSummary:
    check: CheckName
    status: Literal["passed", "failed", "not_applicable"]
    findings: tuple[FindingSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class PackagedFile:
    """One converted output file; ``package_path`` is relative to ``converted/`` in the ZIP."""

    package_path: str
    language: Literal["sql", "python", "json", "yaml"]
    content: str
    etl_format_id: str | None = None


@dataclass(frozen=True, slots=True)
class MappingRef:
    kind: MappingKind
    source: str
    target: str
    note: str = ""
    kb_ref: str | None = None


@dataclass(frozen=True, slots=True)
class PackagedObject:
    object_id: str
    source_qualified_name: str
    target_qualified_name: str
    object_type: str
    source_file: str
    source_line_start: int
    status: ObjectStatus
    confidence: float | None
    agreement: float | None
    pass_rate: float | None
    escalated: bool
    checks: tuple[CheckSummary, ...]
    files: tuple[PackagedFile, ...]
    explanations: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    mappings: tuple[MappingRef, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SessionSummary:
    role: Literal["voter_a", "voter_b", "voter_c", "escalation"]
    batch_index: int
    state: str
    acus: float | None
    url: str | None
    run_id: str = ""
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class EgressSummary:
    consent_at: str
    objects_sent: int
    batches: int
    sessions: int
    blocked_batches: int
    devin_host: str


@dataclass(frozen=True, slots=True)
class PackageInput:
    run_id: str
    package_name: str
    source_platform: str
    target_platform: str
    created_at: str
    confidence_threshold: float
    objects: tuple[PackagedObject, ...]  # dependency (execution) order
    external: tuple[str, ...] = ()
    sessions: tuple[SessionSummary, ...] = ()
    egress: EgressSummary | None = None
    kb_version: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    base_run_id: str | None = None
    reconverted_object_ids: tuple[str, ...] = ()
    kept_base_object_ids: dict[str, str] = field(default_factory=dict)

    def included(self) -> tuple[PackagedObject, ...]:
        """Objects that go into the ZIP: converted and expert_review, in execution order."""
        return tuple(o for o in self.objects if o.status in ("converted", "expert_review"))

    def excluded(self) -> tuple[PackagedObject, ...]:
        return tuple(o for o in self.objects if o.status in ("failed", "cancelled"))
