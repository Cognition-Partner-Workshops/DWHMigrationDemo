from typing import Any

from migraai.planning.sections import SECTIONS


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))


def inventory_summary_block(aggregates: dict[str, Any] | None) -> str:
    if not aggregates or not aggregates.get("objects_by_type"):
        return ""
    return "\n".join(
        [
            "Inventory summary (aggregate counts only)",
            f"Object counts by type: {_format_counts(aggregates['objects_by_type'])}",
            f"Parse-status counts: {_format_counts(aggregates['parse_status_counts'])}",
            f"File counts by detected type: {_format_counts(aggregates['files_by_detected_type'])}",
            f"Total dependencies: {aggregates['total_dependencies']}",
            f"Dependency counts by kind: {_format_counts(aggregates['dependencies_by_kind'])}",
            f"External-reference count: {aggregates['external_reference_count']}",
            f"Issue counts by severity: {_format_counts(aggregates['issues_by_severity'])}",
            f"Total source lines: {aggregates['total_source_lines']}",
        ]
    )


def build_prompt(
    source: str,
    target: str,
    attachment_url: str,
    inventory_aggregates: dict[str, Any] | None = None,
) -> str:
    ordered_sections = "\n".join(f"{section_id}. {title}" for section_id, title in SECTIONS)
    inventory_block = inventory_summary_block(inventory_aggregates)
    estimate_instruction = (
        "For Q, S, and T, set platform_level_estimate to true because only aggregate inventory "
        "counts are available. Ground effort, confidence, and expert-review counts in the "
        "inventory summary."
        if inventory_block
        else "For Q, S, and T set platform_level_estimate to true because object-level discovery "
        "has not happened."
    )
    return f"""Write a data-warehouse migration plan for {source} → {target} using ONLY the
attached context and official vendor documentation.
Every factual statement must cite a KB id from the context or an official vendor
documentation URL.
Return exactly the 21 sections below in order and with these titles:
{ordered_sections}
{estimate_instruction}
Set manual_review to true wherever evidence is uncertain. Do not open repositories, ask
questions, or wait for input. Return the result only through structured output.
You MUST finish by calling provide_structured_output with the complete plan; do not end without it.
{inventory_block}
ATTACHMENT:"{attachment_url}"
"""
