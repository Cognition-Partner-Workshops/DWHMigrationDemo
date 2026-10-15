from .context import ObjectUnderTest, OutputFile, ValidationContext
from .findings import CheckName, CheckResult, Finding, Severity

__all__ = [
    "CheckName",
    "CheckResult",
    "Finding",
    "ObjectUnderTest",
    "OutputFile",
    "Severity",
    "ValidationContext",
    "pass_rate",
    "validate_object",
]


def __getattr__(name: str):
    if name in {"pass_rate", "validate_object"}:
        from .validate import pass_rate, validate_object

        return {"pass_rate": pass_rate, "validate_object": validate_object}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
