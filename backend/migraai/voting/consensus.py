from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .normalise import normalise_files

VOTER_ORDER = ("A", "B", "C")


@dataclass(slots=True)
class Candidate:
    voter: str
    object_id: str
    files: list[dict[str, Any]]
    explanations: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    self_confidence: float = 0.0
    normalized: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConsensusResult:
    picked_voter: str | None
    agreement: float
    pairwise: dict[str, float]
    summed_agreement: dict[str, float] = field(default_factory=dict)


def _tokens(value: str) -> list[str]:
    return re.findall(r"\w+|[^\w\s]", value.casefold(), flags=re.UNICODE)


def agreement(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return SequenceMatcher(None, _tokens(a), _tokens(b)).ratio()


def _normalized(candidate: Any, target: str) -> str:
    if isinstance(candidate, Candidate) and candidate.normalized:
        return candidate.normalized
    if isinstance(candidate, Mapping):
        normalized = candidate.get("normalized", candidate.get("normalized_form"))
        files = candidate.get("files", [])
    else:
        normalized = getattr(candidate, "normalized", None)
        files = getattr(candidate, "files", [])
    return str(normalized) if normalized is not None else normalise_files(files, target)


def _passed_count(result: Any) -> int:
    if result is None:
        return 0
    if isinstance(result, (int, float)) and not isinstance(result, bool):
        return int(result)
    if isinstance(result, Mapping):
        result = result.values()
    if not isinstance(result, (list, tuple, set)):
        result = [result]
    return sum(
        getattr(item, "status", item.get("status") if isinstance(item, Mapping) else None)
        == "passed"
        for item in result
    )


def choose(
    candidates: dict[str, Candidate | dict[str, Any] | None],
    validations: Mapping[str, Any] | None = None,
    *,
    target: str = "snowflake",
) -> ConsensusResult:
    present = {voter: value for voter, value in candidates.items() if value is not None}
    if not present:
        return ConsensusResult(None, 0.0, {})
    normalized = {voter: _normalized(value, target) for voter, value in present.items()}
    pairwise: dict[str, float] = {}
    totals = {voter: 0.0 for voter in present}
    counts = {voter: 0 for voter in present}
    voters = list(present)
    for index, left in enumerate(voters):
        for right in voters[index + 1 :]:
            score = agreement(normalized[left], normalized[right])
            pairwise[f"{left}:{right}"] = score
            totals[left] += score
            totals[right] += score
            counts[left] += 1
            counts[right] += 1
    picked = min(
        voters,
        key=lambda voter: (
            -totals[voter],
            -_passed_count((validations or {}).get(voter)),
            VOTER_ORDER.index(voter) if voter in VOTER_ORDER else len(VOTER_ORDER),
            voter,
        ),
    )
    mean = sum(pairwise.values()) / len(pairwise) if pairwise else 0.0
    return ConsensusResult(picked, mean, pairwise, totals)


__all__ = ["Candidate", "ConsensusResult", "agreement", "choose"]
