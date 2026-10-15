from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migraai.kb.search import get_pair, hybrid_search
from migraai.kb.store import connect
from migraai.planning.context import SECTION_KB_SECTIONS, SECTION_QUERIES, compatibility_matrix
from migraai.planning.schema import Plan
from migraai.planning.sections import SECTIONS
from migraai.registry.platforms import SOURCES, TARGETS

PAIR_FIELDS = {
    "C": "recommended_strategy",
    "G": "object_mapping",
    "R": "risks",
}
CLAIM_SECTIONS = {"H", "J", "K", "L", "O", "P", "U"}
TEMPLATES = {
    "D": "Plan discovery, design, build, validation, and cutover phases for {source} → {target}.",
    "E": (
        "Confirm platform access, network connectivity, security, tooling, and operational "
        "dependencies for {source} → {target}."
    ),
    "F": (
        "Deliver a migration inventory, converted artifacts, validation evidence, runbooks, "
        "and cutover plan for {source} → {target}."
    ),
    "M": (
        "Validate row counts, data distributions, constraints, and business rules between "
        "{source} and {target}."
    ),
    "N": (
        "Test representative workloads, edge cases, performance, recovery, and cutover "
        "procedures for {source} → {target}."
    ),
    "Q": "Platform-level estimate; refined after object discovery.",
    "S": "Platform-level estimate; refined after object discovery.",
    "T": "Platform-level estimate; refined after object discovery.",
}
PLATFORM_NAMES = {item["id"]: item["name"] for item in (*SOURCES, *TARGETS)}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2)


def _platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform.replace("_", " ").title())


def _pair_heading(source: str, target: str) -> str:
    return f"{_platform_name(source)} → {_platform_name(target)} differences"


def _claim_bullet(text: str, url: str | None, confidence: str = "high", label: str = "") -> str:
    claim = text.split("] ", 1)[-1].strip()
    if label:
        claim = f"{label}{claim}"
    source = f" ([source]({url}))" if url else ""
    manual_review = " **(Manual review)**" if confidence.casefold() == "low" else ""
    return f"- {claim}{source}{manual_review}"


def _render_claim_groups(
    groups: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[str, list[dict[str, Any]]]:
    content = []
    selected = []
    for heading, rows in groups:
        rows = rows[:12]
        if not rows:
            continue
        content.append(
            f"### {heading}\n\n"
            + "\n".join(
                _claim_bullet(
                    str(row["text"]),
                    row.get("url"),
                    str(row.get("confidence") or "high"),
                )
                for row in rows
            )
        )
        selected.extend(rows)
    return "\n\n".join(content), selected


def build_local_draft(source: str, target: str, db_path: Path) -> Plan:
    pair = get_pair(source, target, db_path=db_path)
    with connect(db_path) as connection:
        profile = connection.execute(
            """SELECT id,text,url,claim_confidence AS confidence,platform FROM chunks
            WHERE platform IN (?,?)
            AND lower(section) LIKE '%architect%' ORDER BY id""",
            (source, target),
        ).fetchall()
        pair_architecture = connection.execute(
            """SELECT id,text,url,claim_confidence AS confidence FROM chunks
            WHERE source=? AND target=? AND section='architecture_overview' ORDER BY id""",
            (source, target),
        ).fetchall()
        major_differences = connection.execute(
            """SELECT id,text,url,claim_confidence AS confidence,section FROM chunks
            WHERE source=? AND target=?
            AND section IN ('storage','compute','security','scalability')
            ORDER BY section,id""",
            (source, target),
        ).fetchall()
        mapping_ids = {}
        for row in connection.execute(
            """SELECT id,url,text FROM chunks
            WHERE source=? AND target=? AND section='object_mapping'""",
            (source, target),
        ):
            raw_text = str(row["text"]).split("] ", 1)[-1]
            mapping_ids[(str(row["url"]), raw_text)] = int(row["id"])
        compatibility_claims = connection.execute(
            """SELECT id FROM chunks WHERE platform IN (?,?)
            AND section IN ('type_mappings','function_mappings') ORDER BY id""",
            (source, target),
        ).fetchall()
    search_hits = {
        section_id: hybrid_search(
            SECTION_QUERIES[section_id],
            source=source,
            target=target,
            section=SECTION_KB_SECTIONS[section_id],
            k=6,
            db_path=db_path,
        )
        for section_id, _ in SECTIONS
        if section_id in CLAIM_SECTIONS
    }
    hit_ids = {hit.chunk_id for hits in search_hits.values() for hit in hits}
    hit_confidence = {}
    if hit_ids:
        placeholders = ",".join("?" for _ in hit_ids)
        with connect(db_path) as connection:
            hit_confidence = {
                int(row["id"]): str(row["confidence"])
                for row in connection.execute(
                    "SELECT id,claim_confidence AS confidence FROM chunks "
                    f"WHERE id IN ({placeholders})",
                    tuple(hit_ids),
                )
            }
    sections = []
    for section_id, title in SECTIONS:
        citations = []
        if section_id == "A":
            groups = [
                (
                    _platform_name(platform),
                    [dict(row) for row in profile if row["platform"] == platform],
                )
                for platform in (source, target)
            ]
            groups.append((_pair_heading(source, target), [dict(row) for row in pair_architecture]))
            content, selected = _render_claim_groups(groups)
            citations.extend(
                {"kb_id": int(row["id"]), "note": "Platform architecture profile"}
                for row in selected
                if "platform" in row
            )
            citations.extend(
                {"kb_id": int(row["id"]), "note": "Pair architecture overview"}
                for row in selected
                if "platform" not in row
            )
            if not content:
                content = "No curated knowledge for this pair yet."
            elif not pair_architecture:
                content += f"\n\n### {_pair_heading(source, target)}\n\n"
                content += "No curated knowledge for this pair yet."
        elif section_id == "B":
            group = [dict(row) for row in major_differences]
            rendered, selected = _render_claim_groups([(_pair_heading(source, target), group)])
            content = rendered or "No curated knowledge for this pair yet."
            citations.extend(
                {"kb_id": int(row["id"]), "note": "Major platform difference"} for row in selected
            )
        elif section_id in PAIR_FIELDS:
            value = pair.get(PAIR_FIELDS[section_id])
            if value is None:
                content = "No curated knowledge for this pair yet."
            elif section_id == "G":
                bullets = []
                for mapping in value[:12]:
                    claim = mapping.get("claim", {})
                    claim_text = str(claim.get("text", ""))
                    claim_id = mapping_ids.get((str(claim.get("url", "")), claim_text))
                    label = (
                        f"**{mapping.get('source_object', '')} → "
                        f"{mapping.get('target_object', '')}:** "
                    )
                    bullets.append(
                        _claim_bullet(
                            claim_text,
                            claim.get("url"),
                            str(claim.get("confidence") or "high"),
                            label,
                        )
                    )
                    if claim_id is not None:
                        citations.append({"kb_id": claim_id, "note": "Object mapping evidence"})
                content = (
                    f"### {_pair_heading(source, target)}\n\n" + "\n".join(bullets)
                    if bullets
                    else "No curated knowledge for this pair yet."
                )
            elif section_id == "R":
                risks = (
                    [
                        f"- **{name} ({risk.get('level', '')}):** {risk.get('notes', '')}"
                        for name, risk in value.items()
                        if isinstance(risk, dict)
                    ][:12]
                    if isinstance(value, dict)
                    else []
                )
                content = (
                    f"### {_pair_heading(source, target)}\n\n" + "\n".join(risks)
                    if risks
                    else "No curated knowledge for this pair yet."
                )
            else:
                content = f"### {_pair_heading(source, target)}\n\n- {_text(value)}"
        elif section_id in CLAIM_SECTIONS:
            hits = search_hits[section_id]
            groups = []
            for platform in (source, target):
                rows = [
                    {
                        "text": hit.text,
                        "url": hit.url,
                        "confidence": hit_confidence.get(hit.chunk_id, "high"),
                        "id": hit.chunk_id,
                    }
                    for hit in hits
                    if hit.platform == platform
                ]
                groups.append((_platform_name(platform), rows))
            pair_rows = [
                {
                    "text": hit.text,
                    "url": hit.url,
                    "confidence": hit_confidence.get(hit.chunk_id, "high"),
                    "id": hit.chunk_id,
                }
                for hit in hits
                if hit.platform is None
            ]
            groups.append((_pair_heading(source, target), pair_rows))
            content, selected = _render_claim_groups(groups)
            content = content or "No curated knowledge for this pair yet."
            citations.extend(
                {"kb_id": row["id"], "note": f"Knowledge-base claim for {title}"}
                for row in selected
            )
        elif section_id == "I":
            content = f"### {_pair_heading(source, target)}\n\n" + compatibility_matrix(
                source, target, db_path
            )
            citations.extend(
                {"kb_id": int(row["id"]), "note": "Platform compatibility mapping"}
                for row in compatibility_claims
            )
        elif section_id in TEMPLATES:
            content = f"### {_pair_heading(source, target)}\n\n- " + TEMPLATES[section_id].format(
                source=_platform_name(source),
                target=_platform_name(target),
            )
        else:
            content = "No curated knowledge for this pair yet."
        sections.append(
            {
                "id": section_id,
                "title": title,
                "content": content,
                "citations": citations,
                "platform_level_estimate": section_id in {"Q", "S", "T"},
                "manual_review": section_id in {"Q", "S", "T"},
            }
        )
    return Plan.model_validate(
        {"source": source, "target": target, "overall_confidence": None, "sections": sections}
    )
