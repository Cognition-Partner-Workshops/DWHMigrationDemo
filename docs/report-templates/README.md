# Report Templates: Annotated Sample Outputs

This folder holds one complete set of MigraAI report outputs and explains every field in them. The samples come from a mock conversion of the synthetic Teradata test pack (`testpacks/teradata`) to Snowflake. They were produced by the same application code that builds a real package, so they show exactly what a user receives.

| Sample | What it is | Where it appears in the app |
| --- | --- | --- |
| [samples/migration-plan.pdf](samples/migration-plan.pdf) | Migration plan PDF, sections A–U | "Download PDF" on the planning screen |
| [samples/session-report.md](samples/session-report.md) | Session report, Markdown | `report/session-report.md` in the converted ZIP |
| [samples/session-report.pdf](samples/session-report.pdf) | Session report, PDF (same content) | `report/session-report.pdf` in the converted ZIP |
| [samples/mapping-workbook.xlsx](samples/mapping-workbook.xlsx) | Mapping workbook | `mapping/mapping-workbook.xlsx` in the converted ZIP |
| [samples/manifest.json](samples/manifest.json) | Package manifest | `manifest.json` at the root of the converted ZIP |

The converted ZIP is named `<name>-converted.zip` and has one root folder, `<name>-converted/`. For this sample the name is `migraai-teradata-to-snowflake`.

About the sample run:

- The mock Devin client answered all conversion sessions. No data left the machine and no Devin PAT was used.
- Run ID is `sample-teradata-snowflake` and every timestamp is `2026-01-01T00:00:00Z`, so the files do not change between runs.
- Session links have the form `mock://golden/session-0000`. In a real run they are the Devin session URLs.
- All 56 objects converted, so sections that list review, failed or re-converted items are empty in this sample. This guide still explains what they show when they are filled.

## Migration plan PDF

The sample is a **local draft**: it was built from the local knowledge base (KB) only, with no Devin session. The app produces a local draft when no Devin token is configured, and falls back to one when the Devin plan session times out, asks for input, returns an invalid plan or ends early. A plan produced by Devin has the same sections; its header shows the Devin session instead of the local-draft reason.

### Header and table of contents

- **MigraAI Migration Plan**: title.
- **Source → target**: platform IDs, here `teradata -> snowflake`.
- **Date**: when the plan was saved.
- **KB version**: version of the local KB used to build the plan.
- **Origin**: `local_draft` for a local draft, as in this sample; `devin` for a plan produced by Devin.
- **Reason line**: why a local draft was produced, here `Devin token not configured`. Other reasons include `Devin session timed out`, `Devin asked for input` and `Devin returned an invalid plan`. A Devin plan shows `Devin session: <id>` instead.
- **Table of Contents**: the 21 sections A–U in order.

### Section content

Most sections group their points under three headings: the source platform, the target platform, and `<Source> → <Target> differences` for pair-specific points. A point ending in `(source)` links to the documentation page it is based on. A point marked **(Manual review)** comes from a low-confidence KB claim. A section with no KB content says `No curated knowledge for this pair yet.`

Each section ends with a numbered citation list. An entry such as `KB-3733 — Knowledge-base claim for Optimization Hints` names the KB record behind a point and why it was used.

| Section | Content in a local draft |
| --- | --- |
| A. Architecture Overview | Architecture claims for the source, the target, and the pair |
| B. Major Differences | Pair claims on storage, compute, security and scalability |
| C. Recommended Migration Strategy | The pair's recommended strategy from the KB |
| D. Migration Phases | Standard text: discovery, design, build, validation and cutover phases |
| E. Dependencies | Standard text: platform access, network, security, tooling and operational dependencies |
| F. Deliverables | Standard text: inventory, converted artifacts, validation evidence, runbooks and cutover plan |
| G. Object Mapping | Up to 12 source object → target object mappings, each with its evidence |
| H. SQL Dialect Differences | Top KB search results on dialect differences |
| I. Compatibility Matrix | Table with `Kind` (type or function), `Canonical` name, `Source native` and `Target native` forms, with documentation links |
| J. Data Movement Plan | Top KB search results on data movement |
| K. ETL Migration Plan | Top KB search results on ETL migration |
| L. Security Migration Plan | Top KB search results on security migration |
| M. Validation Strategy | Standard text: row counts, distributions, constraints and business rules |
| N. Testing Strategy | Standard text: workloads, edge cases, performance, recovery and cutover tests |
| O. Unsupported Features | Top KB search results on unsupported features |
| P. Workarounds | Top KB search results on workarounds |
| Q. Effort Estimation | Platform-level estimate (see below) |
| R. Risk Assessment | Up to 12 pair risks, each with a level (`high`, `medium`, `low`) and a note |
| S. Confidence Scoring | Platform-level estimate (see below) |
| T. Expert Review Items | Platform-level estimate (see below) |
| U. Optimization Hints | Top KB search results on target optimization |

Sections Q, S and T are platform-level estimates that always need manual review. Without discovered objects they say `Platform-level estimate; refined after object discovery.` When the session has an inventory, as in this sample, they are computed from the inventory counts:

- **Q** gives approximate person-days from a fixed weight per object type, and lists the object count per type. The sample shows `approximately 42.6 person-days across 57 objects`. The count includes the one external reference.
- **S** gives the share of objects that parsed fully, plus the unparsed and partial shares.
- **T** gives the number of unparsed objects, partial objects and external references, and parser issues by severity.

## Session report

The session report is generated once per package, as Markdown and as PDF with the same content. It has nine numbered sections.

### 1. Summary

- **Package**: package name; the ZIP is `<package>-converted.zip`.
- **Run ID**: ID of the conversion run.
- **Created**: package creation time (UTC).
- **Total objects**: every object in the run.
- **converted**, **expert_review**, **failed**, **cancelled**: object count per final status.
- **Included in package**: `converted` plus `expert_review` objects. Failed and cancelled objects have no converted file.
- The remaining lines are the run's own counters, for example `converted`, `escalated`, `expert review` and `failed`. `escalated` counts objects that went to the escalation session.

### 2. Source and target

- **Source platform** and **Target platform**: platform IDs.
- **Confidence threshold**: below this confidence an object goes to expert review. Set in Settings; the default is 85%.
- **KB version**: KB version recorded for the session, or `n/a` if none was recorded. It shows `n/a` in this sample (see the PR notes).

### 3. Scope and inventory

- **Object type / Count** table: number of objects per type, sorted by type.
- **Source files**: the uploaded files the objects came from, by file name.
- **External references**: objects that the code references but that are not in the uploaded files. They are not converted. The sample has one, `sales.customer_archive`.

### 4. Data sent to Devin

If nothing was sent, the section says `No data was sent to Devin.` Otherwise it lists:

- **Consent given at**: when the user confirmed the send to Devin. The user must consent before each send.
- **Devin host**: the Devin API host used. The sample shows `local mock`.
- **Objects sent**: number of objects sent. Secrets are masked before sending.
- **Batches**: number of batches sent.
- **Sessions**: number of Devin sessions started. Each batch uses three voter sessions, plus escalation sessions when needed.
- **Total ACUs**: sum of the ACUs reported for the sessions. The Devin v1 API returns no ACU figure, so this reads `not reported by the Devin API`. If only some sessions reported ACUs, the total ends with `(partial)`.
- **Blocked batches**: batches that were not sent because they still contained secrets. If above zero, a `WARNING` line follows.

Replayed runs (sessions answered from recorded outputs) do not count towards objects sent, batches or sessions.

After a "Re-convert selected" run, two more lines appear:

- `Re-converted <n> selected objects in run <run>; other results carried over from run <base run>.`
- If some selected objects kept their earlier result: `<n> of them kept their earlier result because the re-conversion was cancelled or produced no usable candidate: <names>`.

The sessions table has one row per Devin session:

| Column | Meaning |
| --- | --- |
| Run | Run that started the session. Shown only after "Re-convert selected" or when a session was replayed. |
| Role | `voter_a`, `voter_b`, `voter_c` (the three voters) or `escalation` |
| Batch | Batch number, starting at 0 |
| State | Final session state, for example `done`. A replayed session shows `replayed, nothing sent`. |
| ACUs | ACUs for the session, or `n/a` when not reported |
| Link | Link to the session |

### 5. Conversion results

One row per object:

| Column | Meaning |
| --- | --- |
| Name | Source object name |
| Type | Object type, for example `table`, `view`, `procedure`, `grant` |
| Status | `converted`, `expert_review`, `failed` or `cancelled` |
| Confidence % | Confidence of the chosen conversion |
| Agreement % | How far the voter outputs agreed |
| Escalated | `yes` if the object went to the escalation session |

### 6. Validation

The checks table covers the included objects:

| Column | Meaning |
| --- | --- |
| Check | Validator: `syntax`, `lint`, `semantic`, `rule`, `dependency`, `object_reference`, `security` |
| Passed | Objects that passed the check |
| Failed | Objects that failed the check |
| N/A | Objects the check does not apply to |
| Pass rate | Passed ÷ (Passed + Failed); `n/a` if both are zero |

The **Top findings** table lists the ten most frequent validator findings:

| Column | Meaning |
| --- | --- |
| Rule | Rule ID, for example `snowflake.rule.no_create_index` |
| Check | Validator that raised it |
| Severity | `error` if any occurrence was an error, otherwise the reported severity, such as `warning` |
| Count | Number of occurrences |

A failed check does not always mean expert review. In the sample, objects with failed `syntax` or `rule` checks still converted with confidence above the threshold.

### 7. Expert review and failed items

`None.` when every object converted. Otherwise a table with **Name**, **Status** and **Reason**:

- `expert_review`: the reasons, joined by `;` — confidence below the threshold, the failed checks, and `escalated`. If none applies: `flagged for expert review`.
- `failed`: the recorded error, or `no error recorded`.
- `cancelled`: the recorded error, or `cancelled before completion`.

### 8. Assumptions, unsupported constructs and masked secrets

- **Assumptions**: `<object>: <assumption>` for each assumption made during conversion.
- **Unsupported constructs**: `<object>: <construct>` for each construct with no target equivalent.
- **Masked secrets**: one `WARNING` line per object whose converted file contains `***MASKED***`. Secrets are masked before anything is sent to Devin, so the placeholder must be replaced with a secret reference before running. The sample has three: the users `analyst` and `loader`, and `load_script.sql`.

### 9. Execution order and next steps

- A numbered list of the included objects in execution order: `<target name> (<type>, <status>) -> <converted file>`.
- **Next steps**: review expert-review files (they carry a `NEEDS-REVIEW` header); replace every `***MASKED***` placeholder; run the runner in `execution/<target>/` first in its default dry-run mode, then with `--execute`; add `--include-review` only after the expert-review files are approved.
- MigraAI never connects to the target warehouse. The runner uses the target CLI's own configuration.

## Mapping workbook

The workbook has six sheets. The Objects sheet and the four mapping sheets are also in the ZIP as CSV files under `mapping/csv/` (`objects.csv`, `data_types.csv`, `functions.csv`, `syntax.csv`, `security.csv`). A text cell that starts with `=`, `+`, `-` or `@` gets a leading `'` so Excel does not run it as a formula.

### Summary sheet

Columns **Field** and **Value**:

| Field | Value |
| --- | --- |
| Run ID | Conversion run ID |
| Package name | Package name |
| Source platform | Source platform ID |
| Target platform | Target platform ID |
| Migration | `<source> → <target>` |
| Created at | Package creation time (UTC) |
| Confidence threshold | Threshold as a fraction, for example `0.85` |
| KB version | KB version recorded for the session; empty if none |
| Objects total | Number of objects in the run |
| Converted | Objects with status `converted` |
| Expert review | Objects with status `expert_review` |
| Failed | Objects with status `failed` |
| Cancelled | Objects with status `cancelled` |

### Objects sheet

One row per object. The Status cell is coloured by value (`converted`, `expert_review`, `failed`).

| Column | Meaning |
| --- | --- |
| Source name | Source object name |
| Target name | Object name on the target, for example `SALES.CUSTOMER` |
| Type | Object type |
| Source file | Uploaded file the object came from |
| Status | `converted`, `expert_review`, `failed` or `cancelled` |
| Confidence | Confidence from 0 to 1 |
| Agreement | Voter agreement from 0 to 1 |
| Pass rate | Passed checks ÷ checks that passed or failed, from 0 to 1 (`not_applicable` checks are left out) |
| Escalated | `yes` or `no` |
| Syntax, Lint, Semantic, Rule, Dependency, Object reference, Security | Result of each validator: `passed`, `failed` or `not_applicable`; empty if the check did not run |
| Output files | Converted file(s) holding the object, joined by `;` |
| Notes | The object's error, if any. For expert-review objects, `NEEDS-REVIEW:` followed by the failed findings, or the confidence below the threshold |

### Data Types, Functions, Syntax and Security sheets

Each sheet lists the mappings of one kind used in the run: data types, functions, syntax rules and security rules. Identical mappings are listed once. All four sheets have the same columns:

| Column | Meaning |
| --- | --- |
| Source | Source data type, function or rule ID |
| Target | Target equivalent or rewrite |
| Note | Explanation, if any |
| KB ref | Documentation link from the KB, if any |
| Used by | Source objects that use the mapping, joined by `;` |

A sheet with no mappings has only the header row, as the Functions sheet does in this sample.

## Manifest

`manifest.json` is compact JSON with sorted keys. It describes the package contents and lets you check file integrity.

These keys are always present:

| Key | Meaning |
| --- | --- |
| `package_name` | Package name |
| `run_id` | Conversion run ID |
| `source_platform`, `target_platform` | Platform IDs |
| `created_at` | Package creation time (UTC) |
| `counts` | One count per object status that occurs, plus `total`, `included`, `excluded` (failed plus cancelled) and `external` |
| `egress` | `{"total_acus": ...}` with the same ACU text as the session report; `null` if nothing was sent to Devin |
| `objects` | One entry per object: `id`, `source_name`, `target_name`, `type`, `status`, `confidence` (0 to 1) and `files` (paths inside the ZIP) |
| `excluded` | Failed and cancelled objects: `id`, `name`, `status` and `reason` (the error, or `Object status is <status>.`) |
| `external` | Names of external references |
| `needs_review` | Expert-review objects: `id`, `name`, `files`, and `json_needs_review_flag` (`true` if one of its files is a JSON file) |
| `sha256` | SHA-256 hash of every other file in the ZIP, keyed by path inside the root folder |

These keys appear only in some packages:

| Key | When it appears | Meaning |
| --- | --- | --- |
| `base_run_id` | After "Re-convert selected" | Run whose results were carried over |
| `reconverted_object_ids` | After "Re-convert selected" | IDs of the objects selected for re-conversion |
| `kept_base_object_ids` | After "Re-convert selected" | Selected objects that kept their earlier result because re-conversion was cancelled or gave no usable candidate; maps object ID to the reason (secrets masked). Can be `{}` |
| `sessions` | After "Re-convert selected", or when a session was replayed | One entry per session: `run_id`, `role`, `batch_index`, `state` (`replayed, nothing sent` for replayed sessions), `acus` and `url` |

The sample is a first run with no replay, so none of these four keys is present. Run state is kept in memory only: "Re-convert selected" can merge with an earlier run only while the app stays open.

## How the samples are produced and kept up to date

The samples are generated, not hand-edited. From the repository root, run:

```bash
uv run --project backend python docs/report-templates/generate_samples.py --out docs/report-templates/samples
```

The generator:

1. Builds a temporary local KB from the seed in `backend/migraai/kb/seed`, as the app does at first start.
2. Discovers `testpacks/teradata` and converts it to Snowflake with the mock Devin client, through the same conversion and packaging code as the app.
3. Fixes the run ID, object and session IDs and timestamps so the output does not change between runs.
4. Builds the converted ZIP and copies the session report, the mapping workbook and the manifest out of it unchanged.
5. Builds the migration plan as a local draft and renders it to PDF.
6. Fails if any output contains a local path, a credential or a secret-like value.

The test `backend/tests/test_report_samples.py` runs the generator into a temporary folder and compares the result with the committed samples. The session report, workbook and manifest must match byte for byte. The plan PDF is compared by extracted text, because its internal resource names differ between renders. When the test fails, a change to the report, packaging or planning code has changed the output: rerun the command above, check the differences, update this guide if a field changed, and commit the new samples.
