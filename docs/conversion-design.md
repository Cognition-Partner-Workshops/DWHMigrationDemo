# Conversion engine contract (Phase 4)

This contract binds the core and the three child workstreams: target packs A and B, and ETL outputs. The core owns the prompt templates, the voter output schema, the workflow and the validator framework. Child sessions own only the folders listed in §6.

## 1. Pipeline

```
selected objects → batch planner (topological order, ≤ batch_size, default 20)
  → context builder (redacted text, AST summary, dependency signatures, KB mappings, pair notes, target pack guidance, ETL skeletons)
  → 3 Devin voters per batch (A literal, B idiomatic, C conservative), in parallel up to max_concurrent_sessions
  → split per object → consensus → 7 validators → confidence → escalation (1 session per batch) → re-validate → final | expert_review
```

- **No ACU caps:** neither sessions nor runs are capped. The user decided this. The only stops are a 45-minute per-session timeout and Cancel.
- **No key, no conversion:** without a Devin key, nothing is converted and nothing is sent.

## 2. Shared models (core: `backend/migraai/validation/findings.py`)

```python
CheckName = Literal["syntax", "lint", "semantic", "rule", "dependency", "object_reference", "security"]
Severity = Literal["error", "warning"]

@dataclass(slots=True, frozen=True)
class Finding:
    check: CheckName
    rule_id: str            # e.g. "databricks.lint.no_select_star_in_view"
    severity: Severity
    message: str
    line: int | None = None

@dataclass(slots=True)
class CheckResult:
    check: CheckName
    status: Literal["passed", "failed", "not_applicable"]
    findings: list[Finding]
```

A check fails when it has at least one `error` finding. Warnings are shown but don't fail the check.

## 3. Target pack (`backend/migraai/platforms/targets/<target>/`)

These are the target IDs from `docs/kb-seed-format.md`: `databricks redshift snowflake fabric bigquery synapse clickhouse`.

| File | Content |
|---|---|
| `guidance.md` | At most 1,500 words of target idioms for the prompt context, in these sections in this order: `## Idioms`, `## Required replacements`, `## Unsupported constructs`, `## Procedural code`, `## Security model`, `## ETL and orchestration`. Each bullet ends with a vendor-doc link, `([source](https://...))`. |
| `rules.yaml` | Rules the lint, rule and security validators run (schema below). Each target has at least 12 rules, covering all three checks. |
| `naming.yaml` | Identifier rules for the target (schema below). |
| `fixtures/good/*.<ext>` and `fixtures/bad/*.<ext>` | Synthetic converted code: SQL, `.py` or `.json`. Good files must raise no `error` findings. |
| `fixtures/expected.yaml` | `{<bad file name>: [<rule_id>, ...]}`: the exact set of error rule IDs each bad file must raise. |

### `rules.yaml`

```yaml
target: databricks
rules:
  - id: databricks.lint.no_select_star_in_view    # <target>.<check>.<snake_case>, unique
    check: lint                                   # lint | rule | security
    languages: [sql]                              # sql | python | json | yaml
    object_types: [view]                          # optional; omitted = all types
    kind: forbid_regex                            # forbid_regex | require_regex | forbid_function
    pattern: '(?is)\bcreate\b.*\bview\b.*\bselect\s+\*'  # forbid_regex / require_regex: Python re
    functions: []                                 # forbid_function: case-insensitive function names; matched as \bNAME\s*\(
    severity: error                               # error | warning
    message: "Views must list columns explicitly."
    replacement: "List the columns."              # optional hint shown to the user and to escalation
    claim:                                        # required when check is rule or security; optional for lint
      text: "..."
      url: https://docs.databricks.com/...
      accessed: 2026-10-15
      confidence: high
```

How rules run:
- Comments are stripped before matching: `--` and `/* */` for SQL, `#` for Python.
- The exception is `require_regex` rules whose `id` contains `.explanation`; those match the raw text. This is how the mandatory `-- Converted:` / `-- Reason:` blocks are checked. The core adds one global rule for this, so packs must not.

### `naming.yaml`

```yaml
target: databricks
identifier_case: lower          # lower | upper | preserve
quote_char: "`"                 # quote for identifiers that need quoting
max_identifier_length: 255
qualification: three_part       # three_part (catalog.schema.object) | two_part (schema.object)
reserved_words: [ ... ]         # ≥ 30 target reserved words, upper-case
claims: [<claim>, ...]          # ≥ 2, supporting the values above
```

The claim format is the KB seed claim format (`docs/kb-seed-format.md`). Use only official vendor doc URLs, and never invent one.

### Loader (core: `backend/migraai/platforms/pack.py`)

```python
def load_pack(target: str) -> TargetPack            # pydantic-validated; raises PackError
def evaluate_rules(pack: TargetPack, text: str, *, language: str, object_type: str | None,
                   checks: set[str] | None = None) -> list[Finding]
```

## 4. ETL outputs (`backend/migraai/etl/outputs/`)

```python
@dataclass(slots=True, frozen=True)
class OutputFormat:
    id: str                         # e.g. "databricks.workflows_job"
    target: str
    language: Literal["python", "json", "yaml", "sql"]
    file_suffix: str                # e.g. ".job.json", ".py"
    description: str                # one paragraph for the prompt context
    skeleton: str                   # minimal valid example, used as context

def formats_for(target: str) -> list[OutputFormat]
def validate_output(format_id: str, content: str) -> list[Finding]   # check="syntax"
```

- **Formats:** the outputs follow the Phase-Gate 0 §5 table.
  - Databricks: PySpark notebook `.py` + Workflows job JSON.
  - Redshift, ClickHouse: SQL + Airflow DAG `.py`.
  - Snowflake: SQL + Tasks SQL, plus an Airflow DAG option.
  - Fabric: notebook `.py` + Data Factory pipeline JSON.
  - BigQuery: SQL + Cloud Composer (Airflow) DAG `.py`.
  - Synapse: pipeline JSON + SQL.
- **Python outputs:** `validate_output` uses `ast.parse` only and never imports or executes anything.
- **JSON outputs:** validated with pydantic models defined in this package. Don't add any new dependency; `jsonschema` isn't available.

## 5. Voter output schema (core: `backend/migraai/devin/schemas/conversion.json`)

```json
{"objects": [{
  "object_id": "string",
  "files": [{"rel_path": "string", "language": "sql|python|json|yaml", "content": "string"}],
  "explanations": ["string"],
  "assumptions": ["string"],
  "unsupported": ["string"],
  "self_confidence": 0.0
}]}
```

- An object missing from a voter's output is a failed candidate.
- Confidence = 0.4 × agreement + 0.4 × validation pass rate + 0.2 × mean self-confidence. The threshold is the existing `confidence_threshold` setting (0.85).

## 6. Ownership

| Workstream | Folders |
|---|---|
| Core (lead) | `devin/`, `agents/`, `voting/`, `validation/`, `platforms/pack.py`, `api/`, `frontend/`, `planning/` (import move), this doc |
| Target packs A | `platforms/targets/{databricks,snowflake,bigquery,clickhouse}/`, `tests/unit/platforms/test_pack_{databricks,snowflake,bigquery,clickhouse}.py` |
| Target packs B | `platforms/targets/{redshift,fabric,synapse}/`, `tests/unit/platforms/test_pack_{redshift,fabric,synapse}.py` |
| ETL outputs | `etl/outputs/`, `tests/unit/etl_outputs/` |

**Child rules:**
- Edit only the folders you own. Request any core change in your PR description instead of making it.
- Test file basenames must be unique: tests have no `__init__.py`.
- Use only synthetic code and no real customer code.
