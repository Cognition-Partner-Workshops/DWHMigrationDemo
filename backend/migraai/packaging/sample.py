"""Synthetic PackageInput for Phase 5 generator tests (Teradata -> Snowflake)."""

from __future__ import annotations

from migraai.packaging.model import (
    CheckSummary,
    EgressSummary,
    FindingSummary,
    MappingRef,
    PackagedFile,
    PackagedObject,
    PackageInput,
    SessionSummary,
)

_CHECKS = ("syntax", "lint", "semantic", "rule", "dependency", "object_reference", "security")


def _checks(failed: dict[str, FindingSummary] | None = None) -> tuple[CheckSummary, ...]:
    failed = failed or {}
    return tuple(
        CheckSummary(c, "failed", (failed[c],)) if c in failed else CheckSummary(c, "passed")
        for c in _CHECKS
    )


def sample_package(target: str = "snowflake") -> PackageInput:
    table = PackagedObject(
        object_id="obj-1",
        source_qualified_name="sales.customer_accounts",
        target_qualified_name="SALES.CUSTOMER_ACCOUNTS",
        object_type="table",
        source_file="teradata-testpack.zip/tables.sql",
        source_line_start=1,
        status="converted",
        confidence=0.96,
        agreement=1.0,
        pass_rate=1.0,
        escalated=False,
        checks=_checks(),
        files=(
            PackagedFile(
                "teradata-testpack/tables-converted.sql",
                "sql",
                "-- Converted: MULTISET TABLE to Snowflake table.\n"
                "-- Reason: Snowflake has no SET/MULTISET semantics.\n"
                "CREATE TABLE SALES.CUSTOMER_ACCOUNTS (ID NUMBER(10,0), NAME VARCHAR(100));\n",
            ),
        ),
        explanations=("MULTISET dropped; Snowflake tables allow duplicates.",),
        mappings=(
            MappingRef("object", "sales.customer_accounts", "SALES.CUSTOMER_ACCOUNTS"),
            MappingRef("data_type", "INTEGER", "NUMBER(10,0)", kb_ref="KB-1201"),
            MappingRef("syntax", "MULTISET TABLE", "CREATE TABLE", kb_ref="KB-1310"),
        ),
    )
    view = PackagedObject(
        object_id="obj-2",
        source_qualified_name="sales.v_active",
        target_qualified_name="SALES.V_ACTIVE",
        object_type="view",
        source_file="teradata-testpack.zip/views.sql",
        source_line_start=3,
        status="expert_review",
        confidence=0.71,
        agreement=0.8,
        pass_rate=0.86,
        escalated=True,
        checks=_checks(
            {
                "rule": FindingSummary(
                    "rule", "snowflake.rule.qualify", "warning", "Check QUALIFY.", 4
                )
            }
        ),
        files=(
            PackagedFile(
                "teradata-testpack/views-converted.sql",
                "sql",
                "-- Converted: view.\n-- Reason: ZEROIFNULL to COALESCE.\n"
                "CREATE VIEW SALES.V_ACTIVE AS SELECT ID, COALESCE(BAL, 0) AS BAL "
                "FROM SALES.CUSTOMER_ACCOUNTS;\n",
            ),
        ),
        assumptions=("BAL is numeric.",),
        dependencies=("obj-1",),
        mappings=(MappingRef("function", "ZEROIFNULL(x)", "COALESCE(x, 0)", kb_ref="KB-1402"),),
    )
    grant = PackagedObject(
        object_id="obj-3",
        source_qualified_name="SELECT ON sales.v_active",
        target_qualified_name="SELECT ON SALES.V_ACTIVE",
        object_type="grant",
        source_file="teradata-testpack.zip/security.sql",
        source_line_start=7,
        status="converted",
        confidence=0.95,
        agreement=1.0,
        pass_rate=1.0,
        escalated=False,
        checks=_checks(),
        files=(
            PackagedFile(
                "teradata-testpack/security-converted.sql",
                "sql",
                "-- Converted: grant.\n-- Reason: same privilege.\n"
                "GRANT SELECT ON VIEW SALES.V_ACTIVE TO ROLE REPORTING;\n",
            ),
        ),
        dependencies=("obj-2",),
        mappings=(
            MappingRef("security", "GRANT SELECT ON sales.v_active", "GRANT SELECT ON VIEW"),
        ),
    )
    job = PackagedObject(
        object_id="obj-4",
        source_qualified_name="load_script.sql",
        target_qualified_name="load_script",
        object_type="script",
        source_file="teradata-testpack.zip/load_script.sql",
        source_line_start=1,
        status="failed",
        confidence=None,
        agreement=None,
        pass_rate=None,
        escalated=False,
        checks=(),
        files=(),
        error="All three voter sessions failed.",
    )
    return PackageInput(
        run_id="run-test",
        package_name="teradata-testpack",
        source_platform="teradata",
        target_platform=target,
        created_at="2026-10-16T00:00:00Z",
        confidence_threshold=0.85,
        objects=(table, view, grant, job),
        external=("dbc.tables",),
        sessions=(
            SessionSummary("voter_a", 0, "done", 1.2, "https://devin.example/sessions/a"),
            SessionSummary("voter_b", 0, "done", 1.1, "https://devin.example/sessions/b"),
            SessionSummary("voter_c", 0, "done", 1.3, "https://devin.example/sessions/c"),
            SessionSummary("escalation", 0, "done", 0.8, "https://devin.example/sessions/e"),
        ),
        egress=EgressSummary("2026-10-16T00:00:00Z", 4, 1, 4, 0, "app.devin.ai"),
        kb_version="kb-2026-10-15",
    )
