from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from migraai.core.secret_mask import (
    MASKED_PLACEHOLDER_TOKEN,
    blank_masked_secret_placeholders,
)
from migraai.kb.seed_models import Claim
from migraai.validation.findings import Finding, Severity

Language = Literal["sql", "python", "json", "yaml"]
RuleKind = Literal["forbid_regex", "require_regex", "forbid_function"]
RuleCheck = Literal["lint", "rule", "security"]

TARGETS_DIR = Path(__file__).resolve().parent / "targets"
REQUIRED_GUIDANCE_HEADINGS = (
    "## Idioms",
    "## Required replacements",
    "## Unsupported constructs",
    "## Procedural code",
    "## Security model",
    "## ETL and orchestration",
)
FIXTURE_LANGUAGES: dict[str, Language] = {
    ".sql": "sql",
    ".py": "python",
    ".json": "json",
}


class PackError(ValueError):
    pass


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    check: RuleCheck
    languages: list[Language] = Field(min_length=1)
    object_types: list[str] | None = None
    kind: RuleKind
    pattern: str | None = None
    functions: list[str] = Field(default_factory=list)
    severity: Severity
    message: str = Field(min_length=1)
    replacement: str | None = None
    claim: Claim | None = None

    @model_validator(mode="after")
    def validate_matcher(self) -> Rule:
        if self.kind == "forbid_function":
            if not self.functions:
                raise ValueError("forbid_function rules require at least one function")
            if self.pattern is not None:
                raise ValueError("forbid_function rules must not define a pattern")
        else:
            if self.pattern is None:
                raise ValueError(f"{self.kind} rules require a pattern")
            try:
                re.compile(self.pattern)
            except re.error as error:
                raise ValueError(f"invalid regular expression: {error}") from error
            if self.functions:
                raise ValueError(f"{self.kind} rules must not define functions")
        return self


class Naming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)
    identifier_case: Literal["lower", "upper", "preserve"]
    quote_char: str = Field(min_length=1)
    max_identifier_length: int = Field(gt=0)
    qualification: Literal["three_part", "two_part"]
    reserved_words: list[str] = Field(min_length=30)
    claims: list[Claim] = Field(min_length=2)

    @field_validator("reserved_words")
    @classmethod
    def require_unique_uppercase_words(cls, value: list[str]) -> list[str]:
        if any(word != word.upper() for word in value):
            raise ValueError("reserved_words must be upper-case")
        if len(set(value)) != len(value):
            raise ValueError("reserved_words must be unique")
        return value


class _RulesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)
    rules: list[Rule] = Field(min_length=1)


@dataclass(slots=True)
class TargetPack:
    target: str
    guidance: str
    rules: list[Rule]
    naming: Naming


GLOBAL_RULES = (
    Rule(
        id="core.rule.explanation_block",
        check="rule",
        languages=["sql", "python"],
        kind="require_regex",
        pattern=(
            r"(?ms)(?=.*^\s*--\s*Converted:)(?=.*^\s*--\s*Reason:)"
            r"|(?=.*^\s*#\s*Converted:)(?=.*^\s*#\s*Reason:)"
        ),
        severity="error",
        message="Include both Converted: and Reason: comment markers.",
    ),
)


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise PackError(f"Could not read {path}: {error}") from error


def _validate_guidance(guidance: str, target: str) -> None:
    if len(guidance.split()) > 1500:
        raise PackError(f"{target} guidance.md exceeds 1,500 words")
    headings = [line.strip() for line in guidance.splitlines() if line.startswith("## ")]
    next_heading = 0
    for required in REQUIRED_GUIDANCE_HEADINGS:
        try:
            next_heading = headings.index(required, next_heading) + 1
        except ValueError as error:
            raise PackError(f"{target} guidance.md is missing or misorders {required}") from error


def load_pack(target: str) -> TargetPack:
    if re.fullmatch(r"[a-z_][a-z0-9_]*", target) is None:
        raise PackError(f"Invalid target id: {target!r}")
    pack_dir = TARGETS_DIR / target
    if not pack_dir.is_dir():
        raise PackError(f"Target pack does not exist: {target}")

    try:
        rules_file = _RulesFile.model_validate(_read_yaml(pack_dir / "rules.yaml"))
    except ValidationError as error:
        raise PackError(f"Invalid {target}/rules.yaml: {error}") from error
    try:
        naming = Naming.model_validate(_read_yaml(pack_dir / "naming.yaml"))
    except ValidationError as error:
        raise PackError(f"Invalid {target}/naming.yaml: {error}") from error

    if rules_file.target != target:
        raise PackError(f"rules.yaml target must be {target!r}")
    if naming.target != target:
        raise PackError(f"naming.yaml target must be {target!r}")

    seen_ids: set[str] = set()
    id_pattern = re.compile(rf"{re.escape(target)}\.(?:lint|rule|security)\.[a-z][a-z0-9_]*")
    for rule in rules_file.rules:
        if rule.id in seen_ids:
            raise PackError(f"Duplicate rule id: {rule.id}")
        seen_ids.add(rule.id)
        if id_pattern.fullmatch(rule.id) is None:
            raise PackError(f"Rule id must follow {target}.<check>.<snake_case>: {rule.id}")
        if rule.check in {"rule", "security"} and rule.claim is None:
            raise PackError(f"Rule {rule.id} requires a claim")
        if ".explanation" in rule.id:
            raise PackError("Target packs must not define .explanation rules")

    try:
        guidance = (pack_dir / "guidance.md").read_text(encoding="utf-8")
    except OSError as error:
        raise PackError(f"Could not read {target}/guidance.md: {error}") from error
    _validate_guidance(guidance, target)
    return TargetPack(target=target, guidance=guidance, rules=rules_file.rules, naming=naming)


def _mask_comment_char(char: str) -> str:
    return "\n" if char == "\n" else " "


def _strip_sql_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    quote_end: str | None = None
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quote_end is not None:
            output.append(char)
            if char == "\\" and index + 1 < len(text):
                index += 1
                output.append(text[index])
            elif char == quote_end:
                if following == quote_end:
                    index += 1
                    output.append(following)
                else:
                    quote_end = None
        elif char in {"'", '"', "`"}:
            quote_end = char
            output.append(char)
        elif char == "[":
            quote_end = "]"
            output.append(char)
        elif char == "-" and following == "-":
            output.extend((" ", " "))
            index += 1
            while index + 1 < len(text) and text[index + 1] != "\n":
                index += 1
                output.append(_mask_comment_char(text[index]))
        elif char == "/" and following == "*":
            output.extend((" ", " "))
            index += 1
            depth = 1
            while index + 1 < len(text) and depth:
                index += 1
                if text[index : index + 2] == "/*":
                    output.extend((" ", " "))
                    index += 1
                    depth += 1
                elif text[index : index + 2] == "*/":
                    output.extend((" ", " "))
                    index += 1
                    depth -= 1
                else:
                    output.append(_mask_comment_char(text[index]))
        else:
            output.append(char)
        index += 1
    return "".join(output)


def _strip_python_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    quote_end: str | None = None
    while index < len(text):
        if quote_end is not None:
            if text[index] == "\\" and index + 1 < len(text):
                output.extend((text[index], text[index + 1]))
                index += 2
                continue
            if text.startswith(quote_end, index):
                output.extend(quote_end)
                index += len(quote_end)
                quote_end = None
                continue
            output.append(text[index])
        elif text[index] in {"'", '"'}:
            quote_end = text[index] * 3 if text.startswith(text[index] * 3, index) else text[index]
            output.extend(quote_end)
            index += len(quote_end)
            continue
        elif text[index] == "#":
            while index < len(text) and text[index] != "\n":
                output.append(_mask_comment_char(text[index]))
                index += 1
            if index < len(text):
                output.append("\n")
        else:
            output.append(text[index])
        index += 1
    return "".join(output)


def _strip_comments(text: str, language: str) -> str:
    if language == "sql":
        return _strip_sql_comments(text)
    if language == "python":
        return _strip_python_comments(text)
    return text


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _explanation_pattern(language: str) -> str:
    marker = "--" if language == "sql" else "#"
    marker = re.escape(marker)
    return rf"(?ms)(?=.*^\s*{marker}\s*Converted:)(?=.*^\s*{marker}\s*Reason:)"


def evaluate_rules(
    pack: TargetPack,
    text: str,
    *,
    language: str,
    object_type: str | None,
    checks: set[str] | None = None,
) -> list[Finding]:
    language = language.lower()
    stripped_text = _strip_comments(text, language)
    findings: list[Finding] = []
    for rule in (*pack.rules, *GLOBAL_RULES):
        if language not in rule.languages:
            continue
        if checks is not None and rule.check not in checks:
            continue
        if (
            rule.object_types is not None
            and object_type is not None
            and object_type not in rule.object_types
        ):
            continue

        match_text = text if ".explanation" in rule.id else stripped_text
        pattern = (
            _explanation_pattern(language)
            if rule.id == "core.rule.explanation_block"
            else rule.pattern
        )
        match: re.Match[str] | None = None
        if rule.kind in {"forbid_regex", "require_regex"}:
            match_text = blank_masked_secret_placeholders(match_text)
            match_text = re.sub(
                rf"""(['"]){re.escape(MASKED_PLACEHOLDER_TOKEN)}\1""", "", match_text
            ).replace(MASKED_PLACEHOLDER_TOKEN, "")
            if pattern is not None:
                match = re.search(pattern, match_text)
            if rule.kind == "forbid_regex" and match is None:
                continue
            if rule.kind == "require_regex" and match is not None:
                continue
        else:
            function_matches = [
                found
                for function in rule.functions
                if (
                    found := re.search(
                        rf"\b{re.escape(function)}\s*\(",
                        match_text,
                        re.IGNORECASE,
                    )
                )
                is not None
            ]
            if not function_matches:
                continue
            match = min(function_matches, key=lambda found: found.start())

        findings.append(
            Finding(
                check=rule.check,
                rule_id=rule.id,
                severity=rule.severity,
                message=rule.message,
                line=_line_number(text, match.start()) if match is not None else None,
            )
        )
    return findings


def pack_fixture_results(target: str) -> list[str]:
    pack = load_pack(target)
    pack_dir = TARGETS_DIR / target
    fixtures_dir = pack_dir / "fixtures"
    expected_path = fixtures_dir / "expected.yaml"
    try:
        expected = yaml.safe_load(expected_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        return [f"Could not read {target}/fixtures/expected.yaml: {error}"]
    if not isinstance(expected, dict):
        return [f"{target}/fixtures/expected.yaml must contain a mapping"]

    good_dir = fixtures_dir / "good"
    bad_dir = fixtures_dir / "bad"
    fixture_paths = [
        *((path, False) for path in sorted(good_dir.glob("*")) if path.is_file()),
        *((path, True) for path in sorted(bad_dir.glob("*")) if path.is_file()),
    ]
    bad_names = {path.name for path, is_bad in fixture_paths if is_bad}
    mismatches = [
        f"Expected fixture {name!r} is missing from {target}/fixtures/bad"
        for name in sorted(set(expected) - bad_names)
    ]

    for path, is_bad in fixture_paths:
        language = FIXTURE_LANGUAGES.get(path.suffix.lower())
        if language is None:
            mismatches.append(f"{path.name}: unsupported fixture extension {path.suffix!r}")
            continue
        if is_bad and path.name not in expected:
            mismatches.append(f"{path.name}: missing expected error rule IDs")
            expected_ids: set[str] = set()
        else:
            ids = expected.get(path.name, [])
            if not isinstance(ids, list) or any(not isinstance(rule_id, str) for rule_id in ids):
                mismatches.append(f"{path.name}: expected rule IDs must be a list of strings")
                expected_ids = set()
            else:
                expected_ids = set(ids)

        try:
            source = path.read_text(encoding="utf-8")
        except OSError as error:
            mismatches.append(f"{path.name}: could not read fixture: {error}")
            continue
        actual_ids = {
            finding.rule_id
            for finding in evaluate_rules(pack, source, language=language, object_type=None)
            if finding.severity == "error"
        }
        if actual_ids != expected_ids:
            mismatches.append(
                f"{path.name}: expected errors {sorted(expected_ids)}, got {sorted(actual_ids)}"
            )

    return mismatches
