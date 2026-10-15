from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from migraai.kb.search import get_catalog, get_mappings, get_pair, hybrid_search
from migraai.kb.store import connect
from migraai.planning.sections import SECTIONS

MAX_CONTEXT_BYTES = 200 * 1024
SECTION_QUERIES = {
    "A": "warehouse architecture storage compute deployment",
    "B": "platform migration differences capabilities",
    "C": "recommended migration strategy sequencing",
    "D": "migration phases milestones cutover",
    "E": "dependencies prerequisites integrations",
    "F": "migration deliverables artifacts",
    "G": "object mapping tables views procedures",
    "H": "data types functions syntax differences",
    "I": "type function compatibility equivalents",
    "J": "data movement bulk load transfer",
    "K": "ETL migration pipelines orchestration",
    "L": "security roles permissions encryption",
    "M": "validation reconciliation checks",
    "N": "testing regression performance",
    "O": "unsupported features limitations",
    "P": "workarounds alternatives",
    "Q": "migration effort sizing estimate",
    "R": "migration risks mitigations",
    "S": "confidence evidence uncertainty",
    "T": "expert review manual review",
    "U": "query optimization performance",
}
SECTION_KB_SECTIONS = {
    "A": "architecture_overview",
    "B": None,
    "C": "recommended_strategy",
    "D": "migration_phases",
    "E": "dependencies",
    "F": "deliverables",
    "G": "object_mapping",
    "H": "sql_dialect_differences",
    "I": "compatibility_matrix",
    "J": "data_movement",
    "K": "etl_migration",
    "L": "security_migration",
    "M": "validation_strategy",
    "N": "testing_strategy",
    "O": "unsupported_features",
    "P": "workarounds",
    "Q": "effort_estimation",
    "R": "risks",
    "S": "confidence_scoring",
    "T": "expert_review_items",
    "U": "optimization_hints",
}


@dataclass
class PlanContext:
    markdown: str
    kb_ids: set[int]
    allowed_hosts: set[str]


def compatibility_matrix(source: str, target: str, db_path: Path) -> str:
    grouped: dict[str, dict[str, list[str]]] = {}
    for platform, side in ((source, "source"), (target, "target")):
        for mapping in get_mappings(platform, db_path=db_path):
            if mapping["kind"] not in {"type", "function"}:
                continue
            canonical = mapping["canonical"]
            values = grouped.setdefault(mapping["kind"], {}).setdefault(
                canonical, {"source": [], "target": []}
            )
            values[side].append(f"{mapping['native']} ({mapping['url']})")
    lines = ["| Kind | Canonical | Source native | Target native |", "|---|---|---|---|"]
    for kind in ("type", "function"):
        for canonical, values in sorted(grouped.get(kind, {}).items()):
            target_values = values["target"] or ["no direct equivalent"]
            source_values = values["source"] or ["no direct equivalent"]
            lines.append(
                f"| {kind} | {canonical} | {', '.join(source_values)} | "
                f"{', '.join(target_values)} |"
            )
    return "\n".join(lines)


def build_context(source: str, target: str, db_path: Path) -> PlanContext:
    with connect(db_path) as connection:
        claims = connection.execute(
            """SELECT id,text,url,accessed,section FROM chunks
            WHERE platform=? ORDER BY section,url""",
            (source,),
        ).fetchall()
        target_claims = connection.execute(
            """SELECT id,text,url,accessed,section FROM chunks
            WHERE platform=? ORDER BY section,url""",
            (target,),
        ).fetchall()
        pair_claims = connection.execute(
            """SELECT id,text,url,accessed,section FROM chunks
            WHERE source=? AND target=? ORDER BY section,id""",
            (source, target),
        ).fetchall()
        hosts = {
            str(row["host"]).casefold()
            for row in connection.execute("SELECT host FROM platform_hosts")
        }
    pair = get_pair(source, target, db_path=db_path)
    catalog = get_catalog(source, db_path=db_path)
    hits_by_section = {
        section_id: hybrid_search(
            SECTION_QUERIES[section_id],
            source=source,
            target=target,
            section=SECTION_KB_SECTIONS[section_id],
            k=6,
            db_path=db_path,
        )
        for section_id, _ in SECTIONS
    }

    def render() -> str:
        lines = [
            f"# Migration context: {source} → {target}",
            "## Source profile",
            *[f"- [KB-{row['id']}] {row['text']} ({row['url']})" for row in claims],
            "## Target profile",
            *[f"- [KB-{row['id']}] {row['text']} ({row['url']})" for row in target_claims],
            "## Pair notes",
            *[f"- {key}: {json.dumps(value, ensure_ascii=False)}" for key, value in pair.items()],
            *[f"- [KB-{row['id']}] {row['text']} ({row['url']})" for row in pair_claims],
            "## Source catalog",
            *[f"- {row['tier']} {row['name']}: {row['description']}" for row in catalog],
            "## Compatibility matrix",
            compatibility_matrix(source, target, db_path),
            "## Section-specific knowledge",
        ]
        for section_id, title in SECTIONS:
            lines.append(f"### {section_id}. {title}")
            for hit in hits_by_section[section_id]:
                lines.append(f"[KB-{hit.chunk_id}] {hit.text} ({hit.url})")
        return "\n".join(lines)

    markdown = render()
    while len(markdown.encode("utf-8")) > MAX_CONTEXT_BYTES and any(hits_by_section.values()):
        for section_id, _ in reversed(SECTIONS):
            if hits_by_section[section_id]:
                hits_by_section[section_id].pop()
                break
        markdown = render()
    if len(markdown.encode("utf-8")) > MAX_CONTEXT_BYTES:
        markdown = markdown.encode("utf-8")[:MAX_CONTEXT_BYTES].decode("utf-8", errors="ignore")
    ids = {int(value) for value in re.findall(r"\[KB-(\d+)\]", markdown)}
    return PlanContext(markdown, ids, hosts)
