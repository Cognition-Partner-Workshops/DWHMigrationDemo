# Phase 5 packaging contract

All Phase 5 generators are **pure functions of `migraai.packaging.model.PackageInput`**: a frozen
snapshot of one finished conversion run. They never read the job, the encrypted session store, the
network or the clock. `migraai.packaging.sample.sample_package(target)` is the shared test fixture.

## ZIP layout

`<name>-converted.zip`. `<name>` is the single upload's stem, or the name the user typed for
multi-file uploads (sanitised to `[A-Za-z0-9._-]`, max 80 chars). The default is
`migraai-<source>-to-<target>`.

```
<name>-converted/
  converted/<original folders>/<file>-converted.<ext>   # one file per source file (concatenated objects, source line order)
  mapping/mapping-workbook.xlsx
  mapping/csv/{objects,data_types,functions,syntax,security}.csv
  execution/<target>/...                                 # ordered SQL + runners, dry-run by default
  report/session-report.md
  report/session-report.pdf
  manifest.json
```

- **Included:** only `PackageInput.included()` objects (converted and expert_review), in execution order.
- **Expert-review objects** get a header line in the language's comment syntax: `-- NEEDS-REVIEW: <reason>` for SQL, `# NEEDS-REVIEW: ...` for Python/YAML. JSON can't hold comments, so it is flagged in `manifest.json` only.
- **Failed and cancelled objects** are left out of `converted/` and listed in the manifest and the report.
- **File names:** `tables.sql` becomes `tables-converted.sql`. ETL outputs keep their format's suffix (e.g. `job-converted.py`). On a name collision, add `-2`, `-3`, ….

## Ownership and interfaces

| Module | Owner | Interface |
|---|---|---|
| `packaging/model.py`, `packaging/sample.py` | lead (frozen) | dataclasses + fixture |
| `packaging/snapshot.py`, `packaging/naming.py`, `packaging/zipper.py`, `api/download.py`, frontend Download | lead | `build_package_input(job, session) -> PackageInput`; `build_zip(pkg, parts) -> bytes` |
| `packaging/workbook.py` | child: workbook | `build_workbook(pkg) -> bytes` (XLSX), `workbook_csvs(pkg) -> dict[str, bytes]` (keys: `objects.csv` …) |
| `packaging/scripts.py` + `packaging/runners/` | child: execution scripts | `build_execution_scripts(pkg) -> dict[str, bytes]` (keys relative to `execution/`) |
| `reports/session_report.py` + `reports/templates/` | child: report | `build_report_md(pkg) -> str`, `build_report_pdf(pkg) -> bytes` |

Each child writes only its own files plus its tests, at `backend/tests/unit/packaging/test_<module>*.py` or `backend/tests/unit/reports/`. Test file basenames must be unique. No new dependencies: `openpyxl` is already added; the PDF uses `markdown_it` + `xhtml2pdf` as in `planning/pdf.py`.

## Rules for every generator

- Deterministic output for the same input: fixed timestamps from `pkg.created_at`, and sorted or execution order. For XLSX, set workbook properties `created`/`modified` from `pkg.created_at`.
- Never write source code that wasn't converted, any secret, absolute local paths, settings or API keys.
- Masked placeholders (`***MASKED***`) stay as they are. Wherever they appear, the report and the scripts tell the user to replace them with a secret reference.
- Cell text is escaped against spreadsheet formula injection: prefix values starting with `= + - @` with `'`.
- **MigraAI never connects to a target.** Runners read connection details from the target CLI's own config, profile or environment variables, never from MigraAI. Their default is a dry run that prints the ordered plan. A real run needs an explicit `--execute` flag. Expert-review files are skipped unless `--include-review` is given.

## Session report sections (Markdown and PDF)

1. Summary
2. Source and target
3. Scope and inventory
4. Data sent to Devin: consent time, counts, sessions and ACUs, blocked batches
5. Conversion results: a per-object table of status, confidence, agreement and escalation
6. Validation: pass rate per check, and top findings by rule
7. Expert review and failed items
8. Assumptions, unsupported constructs and masked secrets
9. Execution order and next steps

The PDF uses ASCII `->` arrows (the PDF font can't draw `→`).
