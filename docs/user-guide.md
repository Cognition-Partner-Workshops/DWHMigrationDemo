# MigraAI User Guide

This guide follows the MigraAI screen from top to bottom: System Selection, Migration Planning, Resources Confirmation, Convert Objects (upload, conversion and results), and the download. It then covers the generated runner scripts and gives reference material.

For installation and first start, see [install-guide.md](install-guide.md). For settings, logs, audit and troubleshooting, see [operations-guide.md](operations-guide.md). For a field-by-field description of every output file, see [report-templates/README.md](report-templates/README.md). For the security model, see [security.md](security.md).

## Before you start

MigraAI runs locally. The backend listens only on `127.0.0.1`, and the browser window opened by the launcher is the only client.

The header shows `Local Mode` with one of two lines:

- `Your data stays on this machine`: no Devin key is configured.
- `Conversions are sent to Devin cloud`: a Devin key is configured.

The footer shows `Devin: configured` or `Devin: not configured`, and the reminder `Uploads are deleted on exit`.

Open `Settings` to configure Devin. The fields are:

| Field | Meaning |
| --- | --- |
| `Devin personal access token` | Your Devin API key. Once saved it is shown masked. Tick `Clear saved token` to remove it. |
| `Organization ID` | Your Devin organization ID. Not needed for legacy `apk_` keys. |
| `API base URL` | The Devin API host. |
| `Confidence threshold` | Minimum confidence for an object to be `converted`. Default 0.85 (85%). |
| `Max concurrent sessions` | How many Devin sessions run at the same time. Default 4. |
| `ACU cap per run` | Saved with the settings, but not currently applied to conversion runs. See the FAQ. |

You can plan, upload and explore the inventory without a Devin key. Conversion needs one.

## 1. System Selection

Pick the source and target platforms and press `Confirm`.

`Source Database` offers 8 platforms:

- Teradata
- Oracle
- SQL Server
- Snowflake
- Synapse
- Db2 Warehouse
- Hive
- Netezza

`Target Database` offers 7 platforms:

- Databricks
- Redshift
- Snowflake
- Fabric
- BigQuery
- Synapse
- ClickHouse

A platform cannot be both the source and the target, so Snowflake to Snowflake and Synapse to Synapse are not available.

Pressing `Confirm` starts a new working session for that pair. Any earlier session, with its uploads and results, is discarded and its files are deleted. The rest of the screen unlocks only after `Confirm`.

## 2. Migration Planning

After `Confirm`, MigraAI writes a migration plan for the chosen pair. The section has two tabs: the plan and `Inventory` (see "Inventory and dependency graph" below).

### Plan sections A to U

The plan always has these 21 sections, shown as tabs:

| Section | Title |
| --- | --- |
| A | Architecture Overview |
| B | Major Differences |
| C | Recommended Migration Strategy |
| D | Migration Phases |
| E | Dependencies |
| F | Deliverables |
| G | Object Mapping |
| H | SQL Dialect Differences |
| I | Compatibility Matrix |
| J | Data Movement Plan |
| K | ETL Migration Plan |
| L | Security Migration Plan |
| M | Validation Strategy |
| N | Testing Strategy |
| O | Unsupported Features |
| P | Workarounds |
| Q | Effort Estimation |
| R | Risk Assessment |
| S | Confidence Scoring |
| T | Expert Review Items |
| U | Optimization Hints |

A section can carry a badge such as `Platform-level estimate` (written before any inventory existed) or `Manual review`. If the overall plan confidence is below your threshold, the plan shows `Manual review recommended — overall confidence is below the configured threshold.`

### Devin plan or local draft

When a Devin key is configured, MigraAI starts a Devin session to write the plan as soon as you press `Confirm`; there is no consent dialog for planning. It sends the platform pair and a context file built from MigraAI's knowledge base for that pair. No uploaded source code is sent for planning. While the plan is being written you can press `Cancel`.

If Devin is not configured, or the Devin session fails, times out or returns an unusable plan, MigraAI shows a plan built locally from its knowledge base, marked `Local draft (Devin unavailable)` together with the reason. Press `Retry` to ask Devin again.

A Devin-written plan is saved for the session. Use:

- `Download PDF` to export the plan as a PDF.
- `Regenerate` to write the plan again. With Devin configured it starts a new Devin session, which uses ACUs, and asks you to confirm first. Without Devin it creates a new local draft.
- `Regenerate with inventory` to rewrite the plan using the objects found in your uploads. It appears once the inventory has changed since the plan was written.

## 3. Resources Confirmation

This section lists what a migration of the chosen pair needs, in two groups:

- `Required` (`Required for migration`): the mandatory resources, such as table DDL.
- `Recommended`: optional resources that improve the result, such as grants or ETL jobs.

Each resource shows a description, the number of matching objects discovery found in your uploads, and a status: `Available` or `Awaiting upload`. The counts update after each upload. Use the checkbox on each row to record whether you have that resource; your choice is saved for the session.

## 4. Convert Objects: upload

The Convert Objects section walks through `1. Browse or drop files · 2. Upload Files · 3. Convert Items`.

### Adding files

- Drag files onto `Drag and drop files here`, or press `Browse files` to select several files at once.
- Press `Upload Files` to send the queued files to the local backend. Nothing leaves the machine at this step.
- ZIP files are unpacked locally and each entry is treated as a file. Folder paths inside the ZIP are kept.

Supported file types: `.sql`, `.ddl`, `.txt`, `.zip`, `.xml`, `.dtsx`, `.dsx`, `.py`, `.sh`, `.scala`, `.json`, `.yaml`, `.yml`, `.infa`.

Limits:

- Each file can be at most 100 MB, and the total uploaded per session is also limited to 100 MB.
- A ZIP can hold at most 10,000 entries and 1 GB uncompressed, with a compression ratio of at most 200 to 1.
- A ZIP inside a ZIP is unpacked one level deep only.
- Unsupported entries are rejected. XML files containing a DOCTYPE or entity declaration are rejected.
- Uploading a file with the same path as an earlier one replaces the earlier file.

Uploaded files are never executed.

### File list and discovery

Each file appears in the list with its detected type, size, status, object count and issues. Discovery parses each file to find objects such as tables, views, procedures and ETL jobs. File statuses are `pending`, `queued`, `parsing`, `done`, `partial`, `unparsed`, `rejected` and `failed`. When everything has finished you see `Discovery complete.` (or `Discovery failed.`).

Untick a file to leave its objects out of the inventory and the conversion. Use the delete button to remove a file.

### Inventory and dependency graph

The `Inventory` tab in Migration Planning lists every discovered object.

- Search with `Search object names…` and filter by type, status and file.
- Object statuses are `parsed`, `partial`, `unparsed` and `external`.
- An `external` object is referenced by your code but not defined in any upload, for example a table owned by another system. External objects are listed but never converted. The footer shows the number of objects and external references.
- Select an object to see its `Source preview`, its `Issues`, and its `Upstream` (what it depends on) and `Downstream` (what depends on it) objects.
- Press `Show graph` for the dependency graph around the selected object. Use `Depth` to show 1 to 3 levels, `Fit` to fit the view and `Reset` to restore it. Large graphs stop at 300 objects with `Graph truncated at 300 objects.` Press `Hide graph` to close it.

## 5. Convert Objects: conversion

### Before sending

If no Devin key is configured, pressing `Convert Items` shows `Devin isn't configured. Add a Devin API key in Settings to convert; nothing was sent.` Add the key in `Settings` and try again.

Before you convert, the toolbar shows an estimate for the selected parsed objects, for example `120 objects · 6 batches · up to 24 sessions · no ACU cap`. Objects are grouped into batches of 20. Each batch uses three voter sessions and at most one escalation session, so the maximum is four sessions per batch. No ACU figure is estimated.

Pressing `Convert Items` opens the consent dialog `Send code to Devin?`. It states:

- how many objects' source code will be sent, and to which Devin host;
- that three voter sessions run, up to the maximum number of sessions if escalation is needed;
- that nothing else from your files, settings or Devin key is sent;
- that each session stops after the configured timeout or when you cancel;
- that your consent is recorded in the audit log.

Tick `I understand that this code leaves this computer`, then press `Send to Devin`. Press `Cancel` to close the dialog; nothing is sent.

What is sent, per batch: the source text of each selected object, its metadata, the source-to-target naming map, and short signatures of the objects it depends on. Secrets are masked before sending. Passwords, keys and tokens in the source are replaced with `***MASKED***`, for example `PASSWORD '***MASKED***'`. Your consent is required before every conversion and every re-conversion.

### During the run

The run panel shows the object and batch counts, running totals for converted, escalated, expert review and failed objects, and one card per batch.

Each batch card shows its sessions and their states (`queued`, `running`, `done`, `failed`, `timeout`, `cancelled`, `blocked`):

- `A`, `B` and `C` are three independent Devin voter sessions. Each converts the whole batch on its own.
- `escalation` is an extra session that runs only if some objects in the batch score below the threshold.

At most `Max concurrent sessions` sessions run at once. A session stops after the session timeout (45 minutes by default).

For each object, MigraAI then:

1. Validates all three candidates with the seven validators.
2. Measures how similar the candidates are (agreement) and picks the consensus candidate: the one closest to the other two, then the one passing more checks, then A before B before C.
3. Computes the confidence score (see "Confidence score" below).
4. If the score is below the threshold, sends the object, the three candidates and their findings to the batch's escalation session. The revised candidate is validated and scored again.

Press `Cancel` to stop the run, then `Cancel conversion` to confirm (or `Keep converting`). Running Devin sessions are terminated, objects that had not finished become `cancelled`, and objects that already finished keep their results. You can still download a cancelled run.

You can reload the browser during a run. The page remembers the active job and resumes showing its progress. The run itself lives in the app's memory, so closing the app ends it.

### Results

Each object ends with one status:

- `converted`: confidence is at or above the threshold.
- `expert review`: a usable result exists, but confidence is still below the threshold after escalation. Check it before use.
- `failed`: no usable candidate was produced, or processing failed.
- `cancelled` or `pending`: the run was cancelled before the object finished.

The results table shows, per object: the object, type, confidence, agreement, the seven check chips, and whether it was escalated or needs expert review. The chips are, in order: `syntax`, `lint`, `semantic`, `rule`, `dependency`, `object reference`, `security`. Each chip shows passed, failed or not applicable.

Filters:

- Status: `All`, `converted`, `expert review`, `failed`, `cancelled`, `pending`.
- Type.
- Failed check: one of the seven checks.
- Search: `Object name`.

Select a row to open the detail view:

- Tabs `Final`, `A`, `B` and `C` show the final result and each voter's candidate. On `A`, `B` and `C` each file is shown as a line diff against the final result.
- `Copy` copies a file's content.
- `Explanations`, `Assumptions` and `Unsupported` list what the voters reported.
- `Findings by check` lists each validator finding with its line number where one is known.
- The Devin sessions used for the object are linked.

### Re-convert selected

After a run is done or cancelled, you can convert some objects again:

1. Tick rows in the results table, or press `Select objects needing review` to select the `expert review` objects in the current view.
2. Press `Re-convert selected (<count>)`. The consent dialog counts only the selected objects.
3. The new results are merged into the earlier run. Unselected objects keep their earlier results.

If the re-conversion is cancelled, or produces no usable result for a selected object, that object keeps its earlier result. The screen shows `<count> selected objects kept their earlier result (re-conversion cancelled or not usable)`. The session report and the manifest list these objects too.

Run state is held in memory only. Re-convert selected can merge with an earlier run only while the app stays open. After a restart, or a new `Confirm`, start a fresh conversion.

## 6. Download

Press the `Download Converted Files` button (shown with a download arrow) once the run is done or cancelled and at least one object is `converted` or `expert review`. The button explains why it is disabled when it is.

### ZIP name

- One upload: the ZIP is named after it. `teradata.zip` gives `teradata-converted.zip`; `tables.sql` gives `tables-converted.zip`.
- More than one upload: a `Package name` field appears, prefilled with `migraai-<source>-to-<target>`. The ZIP is `<package name>-converted.zip`. Characters other than letters, digits, `.`, `_` and `-` are replaced.

### What is inside

Everything sits in one top folder, `<name>-converted/`:

| Path | Contents |
| --- | --- |
| `converted/` | The converted files, in the same folders as the uploads. Each file gets a `-converted` name, for example `tables.sql` becomes `tables-converted.sql`. |
| `mapping/mapping-workbook.xlsx` | Mapping workbook with object, data type, function, syntax and security mappings. |
| `mapping/csv/` | The same tables as CSV files: `objects.csv`, `data_types.csv`, `functions.csv`, `syntax.csv`, `security.csv`. |
| `execution/<target>/` | The runner: `order.txt`, `run.sh`, `run.ps1`, `README.md`. |
| `report/session-report.md` and `report/session-report.pdf` | The session report. |
| `manifest.json` | Machine-readable list of every object, its status, files and checksums. |

Rules for the converted files:

- Objects from one source file are written to one converted file, in source line order.
- Files for `expert review` objects start with a header such as `-- NEEDS-REVIEW: Confidence 72% is below the 85% threshold.` (`#` instead of `--` for non-SQL files). JSON files get no header; their review flag is in the manifest.
- `failed` objects are left out of `converted/` but listed, with the reason, in the manifest and the report.

`Report (MD)` and `Report (PDF)` open the session report directly, without downloading the ZIP.

See [report-templates/README.md](report-templates/README.md) for every field in the workbook, CSVs, report and manifest.

## 7. Running the generated scripts

MigraAI never connects to a source or target warehouse. The ZIP contains one runner per target, in `execution/<target>/`, which calls that target's own command-line tool:

| Target | CLI |
| --- | --- |
| Snowflake | `snow` |
| Databricks | `dbsqlcli` |
| BigQuery | `bq` |
| Redshift | `psql` |
| Fabric | `sqlcmd` |
| Synapse | `sqlcmd` |
| ClickHouse | `clickhouse-client` |

The runner reads the connection from the CLI's own profile or environment; MigraAI stores no credentials in it. `order.txt` lists the SQL files in dependency order. ETL and other manual files are not run; `README.md` describes them.

By default the runner only prints the plan (dry run):

```
bash run.sh
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

To apply the files, add `--execute` (`-Execute` in PowerShell). Expert-review files are skipped unless you add `--include-review` (`-IncludeReview`). Arguments after `--` are passed to the CLI.

```
bash run.sh --execute
bash run.sh --execute --include-review
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Execute
```

If a downloaded `run.ps1` is blocked by Windows, run `Unblock-File .\run.ps1` first.

If any file still contains `***MASKED***`, a dry run prints a warning and `--execute` refuses to run, naming each file: `replace the masked secret with a secret reference first`. Replace each placeholder with a secret reference supported by the target, such as a secret object or a key vault reference, then run again. Do not paste plain-text secrets into the files.

## 8. Reference

### Validators

Every candidate is checked by seven validators. A check fails when it reports an error; warnings are shown but do not fail the check. A check that does not apply to an object is shown as not applicable.

| Check | What it checks |
| --- | --- |
| `syntax` | The output parses in the target SQL dialect. Statements the parser cannot interpret are flagged as warnings. |
| `lint` | Target style rules from the target pack, for example no `SELECT *`, no bracket identifiers, no source-dialect functions left in the output. |
| `semantic` | Converted tables keep every source column, each column type maps to an allowed target type, and referenced tables are known. |
| `rule` | Target-specific conversion rules from the target pack, for example Snowflake has no `CREATE INDEX` and `TOP` needs `ORDER BY`. |
| `dependency` | Every object the output depends on is converted in this run or is a known external object. |
| `object reference` | Dependencies are referenced by their mapped target names, not by stale source names. |
| `security` | No secrets in the output; no `GRANT ALL` and no grants to `PUBLIC`; no grant that is missing from or wider than the source grants. A remaining `***MASKED***` placeholder is reported so you replace it before deploying. |

### Confidence score

For each object:

```
confidence = 0.4 x agreement + 0.4 x validator pass rate + 0.2 x mean voter self-confidence
```

- Agreement: how similar the three candidates are after normalisation, from 0 to 1.
- Validator pass rate: passed checks divided by applicable checks for the chosen candidate.
- Self-confidence: the confidence each Devin session reports for its own candidate.

An object at or above the `Confidence threshold` (default 85%) is `converted`. Below it, the object is escalated once; if it is still below the threshold it becomes `expert review`.

### Glossary

| Term | Meaning |
| --- | --- |
| Batch | Up to 20 objects sent to Devin together. |
| Voter | One of three Devin sessions, `A`, `B` or `C`, that convert a batch independently. |
| Candidate | One voter's converted output for an object. |
| Consensus | The candidate chosen from A, B and C. |
| Escalation | An extra Devin session that reviews low-confidence objects and revises them. |
| Expert review | A result that still needs a human check before use. |
| External object | An object referenced by your code but not defined in your uploads. |
| Inventory | All objects found in your uploads by discovery. |
| Masked secret | A secret replaced with `***MASKED***` before sending. |
| Runner | `run.sh` or `run.ps1`, which applies the converted files with the target's CLI. |
| ACU | Agent Compute Unit, the unit Devin uses to bill sessions. |

### FAQ

**What leaves my laptop?**
Without a Devin key, nothing is sent and the plan is a local draft. With a key, two things go to Devin. The plan request sends the platform pair and knowledge-base context, with no uploaded source code. Each conversion, after you consent, sends the selected objects' source code with secrets masked, their metadata, the naming map and dependency signatures. Your other files, settings and Devin key are not sent.

**Is my consent recorded?**
Yes. Every consent is written to the local audit log.

**What does a conversion cost?**
Each batch uses up to four Devin sessions. The Devin API does not return an ACU figure, so the report and manifest show ACUs as "not reported by the Devin API". Check usage in your Devin organization. The `ACU cap per run` setting is not applied to conversions.

**Where is my data kept, and when is it deleted?**
Uploads and results are kept in a temporary session folder on this machine. It is deleted when you press `Confirm` for a new session and when the app exits. Download the ZIP before you close the app.

**Can I continue a run after restarting?**
No. Run state is in memory only.

**Which Windows versions are supported?**
The portable build has been tested only on Windows Server 2022 with an Administrator account, started via `start.cmd` without elevation. Standard-user accounts and Windows 10/11 have not been tested yet. See [install-guide.md](install-guide.md).
