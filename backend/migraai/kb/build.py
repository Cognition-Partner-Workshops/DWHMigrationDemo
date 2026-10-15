from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel

from migraai.kb.embed import Embedder, get_embedder
from migraai.kb.seed_models import (
    Claim,
    PairNotes,
    PlatformMappings,
    PlatformProfile,
    ResourceCatalog,
)
from migraai.kb.store import connect, create_schema, insert_chunk
from migraai.kb.validate import MODELS, validate_seed_dir


def _claim_rows(model: BaseModel) -> list[tuple[Claim, str, str | None, str | None, str | None]]:
    rows = []
    if isinstance(model, PlatformProfile):
        for section, claims in model.facets:
            rows.extend((claim, model.id, None, None, section) for claim in claims)
        rows.extend(
            (claim, model.id, None, None, "unsupported_features")
            for claim in model.unsupported_features
        )
    elif isinstance(model, PlatformMappings):
        for mapping in model.types:
            rows.append((mapping.claim, model.platform, None, None, "type_mappings"))
        for mapping in model.functions:
            rows.append((mapping.claim, model.platform, None, None, "function_mappings"))
    elif isinstance(model, PairNotes):
        source, target = model.source, model.target
        groups = [
            ("architecture_overview", model.architecture_overview),
            ("sql_dialect_differences", model.sql_dialect_differences),
            ("data_movement", model.data_movement),
            ("etl_migration", model.etl_migration),
            ("security_migration", model.security_migration),
            ("unsupported_features", [item.claim for item in model.unsupported_features]),
            ("workarounds", [item.claim for item in model.workarounds]),
            ("optimization_hints", model.optimization_hints),
        ]
        groups.extend((name, claims) for name, claims in model.major_differences)
        groups.append(("object_mapping", [item.claim for item in model.object_mapping]))
        rows.extend(
            (claim, None, source, target, section) for section, claims in groups for claim in claims
        )
    return rows


def build_database(
    seed_dir: Path, out: Path, *, allow_incomplete: bool = False, embedder: Embedder | None = None
) -> Path:
    errors, _ = validate_seed_dir(seed_dir, allow_incomplete=allow_incomplete)
    if errors:
        raise ValueError("\n".join(errors))
    embedder = embedder or get_embedder()
    models: list[BaseModel] = []
    for category, model_type in MODELS.items():
        for path in sorted((seed_dir / category).glob("*.yaml")):
            models.append(
                model_type.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            )
    claims = [
        (claim, platform, source, target, section)
        for model in models
        for claim, platform, source, target, section in _claim_rows(model)
    ]
    vectors = embedder.embed(
        [
            f"[{source or platform} → {target or ''} | {section}] {claim.text}"
            for claim, platform, source, target, section in claims
        ]
    )
    if len(vectors) != len(claims):
        raise RuntimeError("Embedder returned a different number of vectors than input claims.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    with connect(out) as connection:
        create_schema(connection)
        docs: dict[tuple[str | None, str], str] = {}
        now = datetime.now(UTC).isoformat()
        for (claim, platform, source, target, section), vector in zip(claims, vectors, strict=True):
            owner = source or platform
            document_key = (owner, claim.url)
            document_id = docs.get(document_key)
            if document_id is None:
                document_id = str(uuid.uuid4())
                docs[document_key] = document_id
                title = urlsplit(claim.url).path.rsplit("/", 1)[-1] or claim.url
                connection.execute(
                    """INSERT INTO documents
                    (id,url,title,fetched_at,platform,sha256,origin) VALUES(?,?,?,?,?,?,?)""",
                    (document_id, claim.url, title, now, owner, "", "seed"),
                )
            claim_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO claims
                (id,text,url,accessed,applies_to,confidence,platform,source,target,section)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    claim_id,
                    claim.text,
                    claim.url,
                    claim.accessed.isoformat(),
                    claim.applies_to,
                    claim.confidence,
                    platform,
                    source,
                    target,
                    section,
                ),
            )
            prefix = f"[{source or platform}"
            if target:
                prefix += f" → {target}"
            prefix += f" | {section}] "
            insert_chunk(
                connection,
                (
                    document_id,
                    prefix + claim.text,
                    claim.url,
                    claim.accessed.isoformat(),
                    platform,
                    source,
                    target,
                    section,
                    None,
                    claim.confidence,
                ),
                vector,
            )
        for model in models:
            if isinstance(model, PlatformMappings):
                for kind, entries in (("type", model.types), ("function", model.functions)):
                    for item in entries:
                        connection.execute(
                            "INSERT INTO mappings VALUES(?,?,?,?,?,?)",
                            (
                                model.platform,
                                kind,
                                item.native,
                                item.canonical,
                                item.notes,
                                item.claim.url,
                            ),
                        )
            elif isinstance(model, ResourceCatalog):
                for tier, entries in (("mandatory", model.mandatory), ("optional", model.optional)):
                    for item in entries:
                        connection.execute(
                            "INSERT INTO catalog_rows VALUES(?,?,?,?,?,?,?)",
                            (
                                model.source,
                                tier,
                                item.id,
                                item.name,
                                item.description,
                                json.dumps(item.object_types),
                                item.extraction_hint,
                            ),
                        )
            elif isinstance(model, PairNotes):

                def json_default(value):
                    if isinstance(value, BaseModel):
                        return value.model_dump(mode="json", by_alias=True)
                    raise TypeError(f"Cannot serialize {type(value).__name__}")

                for section in ("recommended_strategy", "risks", "object_mapping"):
                    connection.execute(
                        "INSERT INTO pair_text VALUES(?,?,?,?)",
                        (
                            model.source,
                            model.target,
                            section,
                            json.dumps(getattr(model, section), default=json_default),
                        ),
                    )
        connection.execute(
            "INSERT INTO kb_versions(version,created_at,documents,chunks) VALUES(1,?,?,?)",
            (now, len(docs), len(claims)),
        )
        connection.commit()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the MigraAI seed KB database.")
    parser.add_argument("--seed-dir", type=Path, default=Path(__file__).with_name("seed"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    build_database(args.seed_dir, args.out, allow_incomplete=args.allow_incomplete)
    print(f"Built {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
