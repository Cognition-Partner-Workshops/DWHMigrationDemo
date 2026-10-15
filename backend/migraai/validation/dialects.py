TARGET_DIALECTS = {
    "databricks": "databricks",
    "redshift": "redshift",
    "snowflake": "snowflake",
    "fabric": "fabric",
    "bigquery": "bigquery",
    "synapse": "tsql",
    "clickhouse": "clickhouse",
}


def sqlglot_dialect(target: str) -> str:
    try:
        return TARGET_DIALECTS[target.casefold()]
    except KeyError as error:
        raise ValueError(f"Unsupported target dialect: {target}") from error
