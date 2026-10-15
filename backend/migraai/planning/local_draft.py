from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migraai.kb.search import get_pair, hybrid_search
from migraai.kb.store import connect
from migraai.planning.context import SECTION_KB_SECTIONS, SECTION_QUERIES, compatibility_matrix
from migraai.planning.schema import Plan
from migraai.planning.sections import SECTIONS

PAIR_FIELDS = {
    "A": "architecture_overview",
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


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2)


def _table(value: Any) -> str:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        columns = list(value[0])
        lines = [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
        ]
        lines.extend(
            "| " + " | ".join(str(row.get(column, "")) for column in columns) + " |"
            for row in value
        )
        return "\n".join(lines)
    return _text(value)


def _risk_table(value: Any) -> str:
    if isinstance(value, dict) and value:
        rows = [
            {"Risk area": name, "Level": risk.get("level", ""), "Notes": risk.get("notes", "")}
            for name, risk in value.items()
            if isinstance(risk, dict)
        ]
        if rows:
            return _table(rows)
    return _text(value)


def build_local_draft(source: str, target: str, db_path: Path) -> Plan:
    pair = get_pair(source, target, db_path=db_path)
    with connect(db_path) as connection:
        profile = connection.execute(
            """SELECT id,text FROM chunks WHERE platform IN (?,?)
            AND lower(section) LIKE '%architect%' ORDER BY id""",
            (source, target),
        ).fetchall()
        pair_architecture = connection.execute(
            """SELECT id,text FROM chunks
            WHERE source=? AND target=? AND section='architecture_overview' ORDER BY id""",
            (source, target),
        ).fetchall()
        major_differences = connection.execute(
            """SELECT id,text,section FROM chunks
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
    sections = []
    for section_id, title in SECTIONS:
        citations = []
        profile_text = ""
        if section_id == "A":
            profile_text = "\n".join(row["text"] for row in profile)
            citations.extend(
                {"kb_id": int(row["id"]), "note": "Platform architecture profile"}
                for row in profile
            )
            pair_text = "\n".join(row["text"] for row in pair_architecture)
            citations.extend(
                {"kb_id": int(row["id"]), "note": "Pair architecture overview"}
                for row in pair_architecture
            )
            content = "\n\n".join(value for value in (profile_text, pair_text) if value)
            if not pair_architecture:
                content = f"{content}\n\nNo curated knowledge for this pair yet.".strip()
        elif section_id == "B":
            content = "\n\n".join(row["text"] for row in major_differences)
            citations.extend(
                {"kb_id": int(row["id"]), "note": "Major platform difference"}
                for row in major_differences
            )
            content = content or "No curated knowledge for this pair yet."
        elif section_id in PAIR_FIELDS:
            value = pair.get(PAIR_FIELDS[section_id])
            if value is None:
                content = "No curated knowledge for this pair yet."
            elif section_id == "G":
                rows = []
                for mapping in value:
                    claim = mapping.get("claim", {})
                    claim_text = str(claim.get("text", ""))
                    claim_id = mapping_ids.get((str(claim.get("url", "")), claim_text))
                    reference = f" [KB-{claim_id}]" if claim_id is not None else ""
                    rows.append(
                        {
                            "Source object": mapping.get("source_object", ""),
                            "Target object": mapping.get("target_object", ""),
                            "Notes": mapping.get("notes", ""),
                            "Evidence": f"{claim_text}{reference}",
                        }
                    )
                    if claim_id is not None:
                        citations.append({"kb_id": claim_id, "note": "Object mapping evidence"})
                content = _table(rows) if rows else "No curated knowledge for this pair yet."
            elif section_id == "R":
                content = _risk_table(value)
            else:
                content = _text(value)
        elif section_id in CLAIM_SECTIONS:
            hits = hybrid_search(
                SECTION_QUERIES[section_id],
                source=source,
                target=target,
                section=SECTION_KB_SECTIONS[section_id],
                k=6,
                db_path=db_path,
            )
            content = "\n\n".join(hit.text for hit in hits) or (
                "No curated knowledge for this pair yet."
            )
            citations.extend(
                {"kb_id": hit.chunk_id, "note": f"Knowledge-base claim for {title}"} for hit in hits
            )
        elif section_id == "I":
            content = compatibility_matrix(source, target, db_path)
            citations.extend(
                {"kb_id": int(row["id"]), "note": "Platform compatibility mapping"}
                for row in compatibility_claims
            )
        elif section_id in TEMPLATES:
            content = TEMPLATES[section_id].format(source=source, target=target)
        else:
            content = "No curated knowledge for this pair yet."
        if section_id == "A" and not profile_text and section_id not in PAIR_FIELDS:
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
