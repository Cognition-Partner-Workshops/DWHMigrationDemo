from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml
from pydantic import BaseModel, ValidationError
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from migraai.kb.seed_models import (
    Claim,
    PairNotes,
    PlatformMappings,
    PlatformProfile,
    ResourceCatalog,
    SourceSeeds,
)
from migraai.registry.platforms import SOURCES, TARGETS

SOURCE_IDS = {item["id"] for item in SOURCES}
TARGET_IDS = {item["id"] for item in TARGETS}
PLATFORM_IDS = SOURCE_IDS | TARGET_IDS
PAIR_IDS = {(source, target) for source in SOURCE_IDS for target in TARGET_IDS if source != target}
MODELS: dict[str, type[BaseModel]] = {
    "sources": SourceSeeds,
    "platforms": PlatformProfile,
    "catalogs": ResourceCatalog,
    "mappings": PlatformMappings,
    "pairs": PairNotes,
}


def _expected(category: str) -> set[str]:
    if category in {"sources", "platforms", "mappings"}:
        return PLATFORM_IDS
    if category == "catalogs":
        return SOURCE_IDS
    return {f"{source}__{target}" for source, target in PAIR_IDS}


def _format_validation_error(path: Path, error: ValidationError) -> list[str]:
    messages = []
    for issue in error.errors():
        location = ".".join(str(part) for part in issue["loc"]) or "document"
        messages.append(f"{path}: {location}: {issue['msg']}")
    return messages


def _walk_claims(value: Any):
    if isinstance(value, Claim):
        yield value
    elif isinstance(value, BaseModel):
        for item in value.__dict__.values():
            yield from _walk_claims(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_claims(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_claims(item)


def _minimum_errors(path: Path, model: BaseModel) -> list[str]:
    errors: list[str] = []

    def require(field: str, count: int, minimum: int) -> None:
        if count < minimum:
            errors.append(f"{path}: {field} requires at least {minimum}; found {count}")

    if isinstance(model, SourceSeeds):
        require("seeds", len(model.seeds), 5)
        if len(model.seeds) > 10:
            errors.append(f"{path}: seeds allows at most 10; found {len(model.seeds)}")
    elif isinstance(model, PlatformProfile):
        for name, claims in model.facets:
            require(f"facets.{name}", len(claims), 2)
        require("unsupported_features", len(model.unsupported_features), 3)
    elif isinstance(model, ResourceCatalog):
        require("mandatory", len(model.mandatory), 6)
        require("optional", len(model.optional), 3)
        ids = [row.id for row in model.mandatory + model.optional]
        if len(ids) != len(set(ids)):
            errors.append(f"{path}: catalog row ids must be unique")
    elif isinstance(model, PlatformMappings):
        require("types", len(model.types), 15)
        require("functions", len(model.functions), 20)
    elif isinstance(model, PairNotes):
        require("architecture_overview", len(model.architecture_overview), 2)
        for name, claims in model.major_differences:
            require(f"major_differences.{name}", len(claims), 1)
        require("object_mapping", len(model.object_mapping), 5)
        require("sql_dialect_differences", len(model.sql_dialect_differences), 5)
        require("data_movement", len(model.data_movement), 2)
        require("etl_migration", len(model.etl_migration), 2)
        require("security_migration", len(model.security_migration), 2)
        require("unsupported_features", len(model.unsupported_features), 3)
        require("workarounds", len(model.workarounds), 3)
        require("optimization_hints", len(model.optimization_hints), 2)
        feature_ids = [feature.id for feature in model.unsupported_features]
        if len(feature_ids) != len(set(feature_ids)):
            errors.append(f"{path}: unsupported feature ids must be unique")
        unknown_refs = sorted({item.for_id for item in model.workarounds} - set(feature_ids))
        if unknown_refs:
            errors.append(
                f"{path}: workarounds reference unknown features: {', '.join(unknown_refs)}"
            )
        if model.source == model.target:
            errors.append(f"{path}: source and target must differ")
    return errors


def _mapping_entry_lines(path: Path) -> dict[str, list[int]]:
    node = yaml.compose(path.read_text(encoding="utf-8"), Loader=yaml.SafeLoader)
    if not isinstance(node, MappingNode):
        return {}
    fields = {key.value: value for key, value in node.value if isinstance(key, ScalarNode)}
    return {
        field: [item.start_mark.line + 1 for item in value.value]
        for field in ("types", "functions")
        if isinstance((value := fields.get(field)), SequenceNode)
    }


def _duplicate_mapping_errors(
    mappings: list[tuple[Path, BaseModel]],
) -> list[str]:
    locations: dict[
        tuple[str, str, str],
        list[tuple[Path, int, str, str]],
    ] = {}
    for path, model in mappings:
        if not isinstance(model, PlatformMappings):
            continue
        lines_by_field = _mapping_entry_lines(path)
        for kind, field, entries in (
            ("type", "types", model.types),
            ("function", "functions", model.functions),
        ):
            for index, item in enumerate(entries):
                lines = lines_by_field.get(field, [])
                if index >= len(lines):
                    continue
                key = (model.platform.casefold(), kind, " ".join(item.native.split()).casefold())
                locations.setdefault(key, []).append(
                    (path, lines[index], item.native, item.canonical)
                )

    errors: list[str] = []
    for (platform, kind, native), entries in locations.items():
        if len(entries) < 2:
            continue
        first_path, first_line, _, first_canonical = entries[0]
        for path, line, _, canonical in entries[1:]:
            errors.append(
                f"{path}:{line}: duplicate mapping key "
                f"({platform}, {kind}, {native!r}); first declared at "
                f"{first_path}:{first_line} with canonical {first_canonical!r}; "
                f"duplicate canonical {canonical!r}"
            )
    return errors


def _platform_for(category: str, model: BaseModel) -> set[str]:
    if isinstance(model, PairNotes):
        return {model.source, model.target}
    if isinstance(model, PlatformProfile):
        return {model.id}
    if isinstance(model, (SourceSeeds, PlatformMappings)):
        return {model.platform}
    if isinstance(model, ResourceCatalog):
        return {model.source}
    return set()


def _claim_errors(
    path: Path,
    category: str,
    model: BaseModel,
    allowed_hosts: dict[str, set[str]],
) -> list[str]:
    errors: list[str] = []
    owner_ids = _platform_for(category, model)
    hosts = set().union(*(allowed_hosts.get(platform, set()) for platform in owner_ids))
    missing = sorted(platform for platform in owner_ids if platform not in allowed_hosts)
    for claim in _walk_claims(model):
        parsed = urlsplit(claim.url)
        if parsed.scheme != "https":
            errors.append(f"{path}: claim.url must use https: {claim.url}")
        elif parsed.hostname not in hosts:
            errors.append(
                f"{path}: claim host is not allowed for {', '.join(sorted(owner_ids))}: {claim.url}"
            )
        if claim.accessed > date.today():
            errors.append(f"{path}: claim.accessed cannot be in the future: {claim.accessed}")
    if missing and any(_walk_claims(model)):
        errors.append(
            f"{path}: cannot validate claim hosts; missing sources files for {', '.join(missing)}"
        )
    return errors


def _model_id(category: str, model: BaseModel) -> str:
    if isinstance(model, PlatformProfile):
        return model.id
    if isinstance(model, ResourceCatalog):
        return model.source
    if isinstance(model, (SourceSeeds, PlatformMappings)):
        return model.platform
    if isinstance(model, PairNotes):
        return f"{model.source}__{model.target}"
    raise ValueError(category)


def _urls(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "url" and isinstance(item, str) and item.startswith(("http://", "https://")):
                yield item
            yield from _urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from _urls(item)


def check_urls(urls: set[str]) -> list[str]:
    def check(url: str) -> str | None:
        try:
            response = httpx.get(url, timeout=15, follow_redirects=True)
            if response.status_code != 200:
                return f"{url}: HTTP {response.status_code}"
        except httpx.HTTPError as error:
            return f"{url}: {error}"
        return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        return [error for error in executor.map(check, sorted(urls)) if error]


def validate_seed_dir(
    seed_dir: Path, *, allow_incomplete: bool = False
) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    urls: set[str] = set()
    loaded: dict[str, list[tuple[Path, BaseModel]]] = {name: [] for name in MODELS}
    allowed_hosts: dict[str, set[str]] = {}

    for category, model_type in MODELS.items():
        directory = seed_dir / category
        files = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
        stems = {path.stem for path in files}
        expected = _expected(category)
        if not allow_incomplete and stems != expected:
            missing = sorted(expected - stems)
            extra = sorted(stems - expected)
            if missing:
                errors.append(f"{category}: missing ids: {', '.join(missing)}")
            if extra:
                errors.append(f"{category}: unexpected ids: {', '.join(extra)}")

        found_ids: list[str] = []
        for path in files:
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                urls.update(_urls(raw))
                model = model_type.model_validate(raw)
            except (OSError, yaml.YAMLError, ValidationError) as error:
                if isinstance(error, ValidationError):
                    errors.extend(_format_validation_error(path, error))
                else:
                    errors.append(f"{path}: {error}")
                continue
            item_id = _model_id(category, model)
            found_ids.append(item_id)
            if path.stem != item_id:
                errors.append(f"{path}: filename id does not match {item_id}")
            errors.extend(_minimum_errors(path, model))
            if isinstance(model, SourceSeeds):
                if item_id in allowed_hosts:
                    errors.append(f"{path}: duplicate source seed id {item_id}")
                allowed_hosts[item_id] = {host.casefold() for host in model.allowed_hosts}
                for seed in model.seeds:
                    parsed = urlsplit(seed.url)
                    if parsed.scheme != "https":
                        errors.append(f"{path}: seed URL must use https: {seed.url}")
                    if parsed.hostname not in allowed_hosts[item_id]:
                        errors.append(f"{path}: seed host is not allowed: {seed.url}")
            loaded[category].append((path, model))
        if len(found_ids) != len(set(found_ids)):
            errors.append(f"{category}: ids must be unique")

    errors.extend(_duplicate_mapping_errors(loaded["mappings"]))

    profiles = {
        model.id: model for _, model in loaded["platforms"] if isinstance(model, PlatformProfile)
    }
    for platform, profile in profiles.items():
        expected_roles = (["source"] if platform in SOURCE_IDS else []) + (
            ["target"] if platform in TARGET_IDS else []
        )
        if profile.roles != expected_roles:
            errors.append(
                f"{seed_dir / 'platforms' / f'{platform}.yaml'}: roles must be "
                f"{sorted(expected_roles)}"
            )
    for category in ("platforms", "mappings", "pairs"):
        for path, model in loaded[category]:
            errors.extend(_claim_errors(path, category, model, allowed_hosts))

    return errors, urls


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MigraAI KB YAML seed files.")
    parser.add_argument("--seed-dir", type=Path, default=Path(__file__).with_name("seed"))
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--check-urls", action="store_true")
    args = parser.parse_args()
    errors, urls = validate_seed_dir(args.seed_dir, allow_incomplete=args.allow_incomplete)
    if args.check_urls:
        errors.extend(check_urls(urls))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"KB seed validation passed ({len(urls)} distinct URLs).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
