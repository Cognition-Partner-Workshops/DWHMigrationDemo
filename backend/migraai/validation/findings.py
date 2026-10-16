from dataclasses import dataclass
from typing import Literal

CheckName = Literal[
    "syntax",
    "lint",
    "semantic",
    "rule",
    "dependency",
    "object_reference",
    "security",
]
Severity = Literal["error", "warning"]


@dataclass(slots=True, frozen=True)
class Finding:
    check: CheckName
    rule_id: str
    severity: Severity
    message: str
    line: int | None = None


@dataclass(slots=True)
class CheckResult:
    check: CheckName
    status: Literal["passed", "failed", "not_applicable"]
    findings: list[Finding]
