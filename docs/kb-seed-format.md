# Knowledge-base seed format

Seed knowledge ships in `backend/migraai/kb/seed/` as YAML. `python -m migraai.kb.validate` checks it, and CI runs that check. At packaging time the seed is built into `kb-v1.db`.

## Platform IDs

- Sources: `teradata oracle sqlserver snowflake synapse db2 hive netezza`
- Targets: `databricks redshift snowflake fabric bigquery synapse clickhouse`

That gives 13 unique platforms. There are 54 pairs: the 8 × 7 combinations minus `snowflake→snowflake` and `synapse→synapse`.

## Claim (used everywhere)

```yaml
text: "Teradata BYTEINT is a 1-byte signed integer (-128..127)."
url: https://docs.teradata.com/...        # https; host must be in the platform's allowed_hosts
accessed: 2026-10-15                      # YYYY-MM-DD, date the page was read
applies_to: "Vantage 17.20+"              # optional; versions the claim holds for
confidence: high                          # high | medium | low
```

The rules for claims:
- Every factual statement is a claim.
- A claim's `url` must be an official vendor documentation page (or vendor whitepaper or blog) that supports the text.
- Never invent URLs. If you cannot find a supporting page, drop the claim.

## Files

### `sources/<platform>.yaml`: refresh seeds (13 files)

```yaml
platform: teradata
allowed_hosts: [docs.teradata.com, www.teradata.com]
seeds:                       # 5–10 official doc pages
  - url: https://docs.teradata.com/r/...
    path_prefix: /r/...      # the refresh follows depth-1 links only under this prefix on the same host
    topic: sql-data-types
```

### `platforms/<platform>.yaml`: profiles (13 files)

```yaml
id: teradata
name: Teradata Vantage
roles: [source]              # snowflake and synapse: [source, target]
versions_covered: "Vantage 17.x / VantageCloud Lake"
summary: "<2–4 sentences>"
facets:                      # each list ≥ 2 claims
  architecture: [<claim>, ...]
  storage: [...]
  compute: [...]
  security: [...]
  scalability: [...]
  sql_dialect: [...]
  etl_tooling: [...]
  data_movement: [...]
unsupported_features: [<claim>, ...]   # notable features with no direct equivalent elsewhere, ≥ 3
```

### `catalogs/<source>.yaml`: resource catalogs (8 files, sources only)

```yaml
source: teradata
mandatory:                   # ≥ 6 rows: tables DDL, views, procedures/macros, functions/UDFs, security objects, ETL definitions (+ platform-specific)
  - id: ddl_tables           # snake_case, unique within file
    name: Table DDL
    description: "CREATE TABLE statements for all in-scope databases."
    object_types: [table]
    extraction_hint: "SHOW TABLE <db>.<tbl>; or DBC.TablesV + SHOW TABLE scripts"
optional:                    # ≥ 3 rows: statistics, sample data, operational logs (+ platform-specific)
  - ...
```

### `mappings/<platform>.yaml`: type and function mappings (13 files)

For a source, `native` maps to `canonical`. For a target, `canonical` maps to `native`. Snowflake and Synapse use one file for both directions.

```yaml
platform: teradata
types:                       # ≥ 15 rows
  - native: "BYTEINT"
    canonical: int8
    notes: ""
    claim: <claim>
functions:                   # ≥ 20 rows; canonical is written in ANSI SQL
  - native: "ZEROIFNULL(x)"
    canonical: "COALESCE(x, 0)"
    notes: ""
    claim: <claim>
```

The canonical type vocabulary is closed: `int8 int16 int32 int64 decimal float32 float64 boolean char varchar string binary varbinary date time time_tz timestamp timestamp_tz interval json array map struct geography geometry uuid xml period other`.

### `pairs/<source>__<target>.yaml`: pair-difference notes (54 files)

```yaml
source: teradata
target: snowflake
architecture_overview: [<claim>, ...]          # A, ≥ 2
major_differences:                             # B, each ≥ 1
  storage: [...]
  compute: [...]
  security: [...]
  scalability: [...]
recommended_strategy: "<paragraph>"            # C, plain text, no claims needed
object_mapping:                                # G, ≥ 5
  - source_object: "MACRO"
    target_object: "Stored procedure (SQL Scripting)"
    notes: ""
    claim: <claim>
sql_dialect_differences: [<claim>, ...]        # H, ≥ 5
data_movement: [<claim>, ...]                  # J, ≥ 2
etl_migration: [<claim>, ...]                  # K, ≥ 2
security_migration: [<claim>, ...]             # L, ≥ 2
unsupported_features:                          # O, ≥ 3
  - id: ufeat_1
    claim: <claim>
workarounds:                                   # P, ≥ 3; `for` refers to an unsupported_features id
  - for: ufeat_1
    claim: <claim>
risks:                                         # R, all six required
  sql:         {level: medium, notes: "..."}
  data:        {level: low, notes: "..."}
  security:    {level: medium, notes: "..."}
  governance:  {level: low, notes: "..."}
  platform:    {level: medium, notes: "..."}
  operational: {level: low, notes: "..."}
optimization_hints: [<claim>, ...]             # U, ≥ 2; improvements beyond a 1:1 migration
```

Sections D, E, F, M, N, Q, S and T are generic templates in the planning engine and are not part of the seed. Section I, the compatibility matrix, is computed from the mappings.
