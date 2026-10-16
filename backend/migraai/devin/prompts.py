VOTER_BASE = (
    "You are converting database objects from {source} to {target} for MigraAI. The attached "
    "Markdown file contains {count} objects in dependency order, each with its source text "
    "(secrets masked as ***), a structure summary, dependency signatures, type and function "
    "mappings, and the target guidance and rules. Convert every object listed. Do not create pull "
    "requests, do not modify any repository and do not connect to any database. Return only the "
    "structured output matching the schema, with exactly one entry per object_id. Precede every "
    "converted statement with a comment block `-- Converted: <what changed>` and `-- Reason: "
    "<why>` (use `#` instead of `--` in Python). Use the object names from the naming map. If a "
    "construct has no equivalent, convert what you can, list it under `unsupported`, and lower "
    "self_confidence. self_confidence is your 0–1 estimate that the output is correct and runs on "
    "{target}.\n\n{stance}"
)

STANCES = {
    "A": (
        "Stance: stay as literal and faithful to the source as possible; change syntax only where "
        "{target} requires it."
    ),
    "B": (
        "Stance: write idiomatic, best-practice {target} code that returns the same results, using "
        "native features where they are clearly better."
    ),
    "C": (
        "Stance: be conservative and defensive; preserve exact semantics including NULL handling, "
        "ordering, data types and error behaviour, prefer explicit casts, and record anything "
        "uncertain under assumptions."
    ),
}

ESCALATION = (
    "You are adjudicating database object conversions from {source} to {target} for MigraAI. The "
    "attached Markdown file contains {count} objects whose three candidate conversions disagreed "
    "or failed validation. For each object it gives the source text, the three candidates and the "
    "validator findings. Produce the single best corrected conversion for each object, fixing "
    "every finding you can, and explain your decision in explanations. Do not create "
    "pull requests, do not modify any repository and do not connect to any database. Return only "
    "the structured output "
    "matching the schema, with exactly one entry per object_id, following the same comment-block "
    "and naming rules as the candidates."
)
