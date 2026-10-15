import sqlglot
from sqlglot import ErrorLevel, exp
from sqlglot.errors import ParseError

from migraai.validation.context import ObjectUnderTest, ValidationContext
from migraai.validation.dialects import sqlglot_dialect
from migraai.validation.findings import Finding
from migraai.validation.names import identifier_parts, normalize_name


def _table_name(table: exp.Table) -> str:
    return ".".join(
        part
        for part in (
            table.catalog,
            table.db,
            table.name,
        )
        if part
    )


def _line(node: exp.Expression) -> int | None:
    value = node.meta.get("line")
    return value if isinstance(value, int) else None


def _last_two(name: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in identifier_parts(name)[-2:])


def _resolve_name(name: str, known: set[str]) -> str | None:
    normalized = normalize_name(name)
    if normalized in known:
        return normalized
    suffix = _last_two(normalized)
    matches = [candidate for candidate in known if _last_two(candidate) == suffix]
    return matches[0] if len(matches) == 1 else None


def _normalized_type(value: str, target: str) -> exp.DataType | None:
    try:
        return exp.DataType.build(value, dialect=sqlglot_dialect(target))
    except (ParseError, ValueError, TypeError):
        return None


def _zero_scale_decimal(value: exp.DataType) -> bool:
    if value.this != exp.DataType.Type.DECIMAL:
        return False
    if len(value.expressions) < 2:
        return True
    scale = value.expressions[1]
    return scale.is_number and scale.name == "0"


def _compatible_type(left: str, right: str, target: str) -> bool:
    left_type = _normalized_type(left, target)
    right_type = _normalized_type(right, target)
    if left_type is None or right_type is None:
        return left.strip().casefold() == right.strip().casefold()
    if left_type.this == right_type.this:
        return True
    left_integer = left_type.this in exp.DataType.INTEGER_TYPES
    right_integer = right_type.this in exp.DataType.INTEGER_TYPES
    return (left_integer and _zero_scale_decimal(right_type)) or (
        right_integer and _zero_scale_decimal(left_type)
    )


def _source_columns_findings(
    obj: ObjectUnderTest,
    ctx: ValidationContext,
    findings: list[Finding],
) -> None:
    if obj.object_type.casefold() != "table" or not obj.source_column_types:
        return
    target_name = next(
        (
            target
            for source, target in ctx.name_map.items()
            if normalize_name(source) == normalize_name(obj.source_qualified_name)
        ),
        obj.source_qualified_name,
    )
    columns_by_table = {
        normalize_name(table): {
            normalize_name(column): target_type for column, target_type in columns
        }
        for table, columns in ctx.table_columns.items()
    }
    table_key = _resolve_name(target_name, set(columns_by_table))
    target_columns = columns_by_table.get(table_key, {}) if table_key is not None else {}
    for column, source_type in obj.source_column_types:
        target_type = target_columns.get(normalize_name(column))
        if target_type is None:
            findings.append(
                Finding(
                    check="semantic",
                    rule_id="semantic.missing_column",
                    severity="error",
                    message=f"Converted table is missing source column {column}.",
                )
            )
            continue
        if _compatible_type(target_type, source_type, ctx.target):
            continue
        allowed_types = next(
            (
                targets
                for source, targets in ctx.type_map.items()
                if _compatible_type(source_type, source, ctx.target)
                or source.strip().casefold() == source_type.strip().casefold()
            ),
            [],
        )
        if not any(_compatible_type(target_type, allowed, ctx.target) for allowed in allowed_types):
            findings.append(
                Finding(
                    check="semantic",
                    rule_id="semantic.type_mismatch",
                    severity="warning",
                    message=(
                        f"Column {column} maps from {source_type} to {target_type}, "
                        "which is not an allowed target type."
                    ),
                )
            )


def validate(obj: ObjectUnderTest, ctx: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    target_names = {normalize_name(target) for target in ctx.name_map.values()}
    external_names = {normalize_name(name) for name in ctx.external}
    known_columns = {
        normalize_name(table): {normalize_name(column) for column, _ in columns}
        for table, columns in ctx.table_columns.items()
    }
    self_target = next(
        (
            target
            for source, target in ctx.name_map.items()
            if normalize_name(source) == normalize_name(obj.source_qualified_name)
        ),
        obj.source_qualified_name,
    )
    self_name = normalize_name(self_target)
    parsed_files: list[list[exp.Expression]] = []

    for file in obj.files:
        if file.language.casefold() != "sql":
            continue
        try:
            statements = sqlglot.parse(
                file.content,
                read=sqlglot_dialect(ctx.target),
                error_level=ErrorLevel.RAISE,
            )
        except ParseError:
            continue
        parsed_files.append(statements)

    unknown_tables: list[tuple[str, int | None]] = []
    for statements in parsed_files:
        for statement in statements:
            cte_names = {cte.alias_or_name.casefold() for cte in statement.find_all(exp.CTE)}
            table_aliases: dict[str, str] = {}
            for table in statement.find_all(exp.Table):
                name = _table_name(table)
                normalized = normalize_name(name)
                if not normalized or (
                    not table.catalog and not table.db and table.name.casefold() in cte_names
                ):
                    continue
                resolved_target = _resolve_name(name, target_names)
                is_external = _resolve_name(name, external_names) is not None
                is_self = _resolve_name(name, {self_name}) is not None
                if resolved_target is None and not is_external and not is_self:
                    unknown_tables.append((name, _line(table)))
                    continue
                if resolved_target is not None:
                    alias = table.alias_or_name.casefold()
                    table_aliases[alias] = resolved_target

            for column in statement.find_all(exp.Column):
                alias = column.table.casefold()
                target_table = table_aliases.get(alias)
                if target_table is None:
                    continue
                columns = known_columns.get(target_table)
                if columns is None or normalize_name(column.name) in columns:
                    continue
                findings.append(
                    Finding(
                        check="semantic",
                        rule_id="semantic.unknown_column",
                        severity="warning",
                        message=(
                            f"Column {column.name} is not listed for table alias {column.table}."
                        ),
                        line=_line(column),
                    )
                )

    for name, line in unknown_tables:
        findings.append(
            Finding(
                check="semantic",
                rule_id="semantic.unknown_table",
                severity="error",
                message=f"Referenced table {name} is not in the name map or external set.",
                line=line,
            )
        )

    _source_columns_findings(obj, ctx, findings)
    return findings
