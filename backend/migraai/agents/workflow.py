from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypedDict

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from migraai.core import secret_mask
from migraai.devin.attachment import render_batch
from migraai.devin.prompts import ESCALATION, STANCES, VOTER_BASE
from migraai.devin.runner import SessionOutcome, run_session
from migraai.validation.context import ObjectUnderTest, OutputFile, ValidationContext
from migraai.validation.names import is_nameable_object_type
from migraai.validation.validate import pass_rate, validate_object
from migraai.voting.confidence import score
from migraai.voting.consensus import Candidate, choose
from migraai.voting.normalise import normalise_files

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "devin" / "schemas" / "conversion.json"
CONVERSION_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
_RUNTIME_CONTEXTS: dict[str, dict[str, Any]] = {}
_RUNTIME_LOCK = threading.RLock()
_SQLGLOT_LOG_LOCK = threading.RLock()
_SQLGLOT_LOG_USERS = 0
_SQLGLOT_LOG_LEVEL: int | None = None


class WorkflowState(TypedDict, total=False):
    run_key: str
    batch_index: int
    source: str
    target: str
    name_map: dict[str, str]
    confidence_threshold: float
    attachment_md: str
    conversion_objects: list[dict[str, Any]]
    voter_outcomes: dict[str, SessionOutcome]
    candidates: dict[str, dict[str, Candidate | None]]
    object_errors: dict[str, str]
    candidate_validations: dict[str, dict[str, list[Any]]]
    consensus: dict[str, Any]
    picked_validations: dict[str, list[Any]]
    scores: dict[str, float]
    to_escalate: list[str]
    escalation_outcome: SessionOutcome | None
    escalation_candidates: dict[str, Candidate | None]
    escalated: bool
    results: dict[str, dict[str, Any]]
    error: str | None


def _runtime(state: WorkflowState) -> dict[str, Any]:
    with _RUNTIME_LOCK:
        return _RUNTIME_CONTEXTS[state["run_key"]]


def _update_session_progress(
    state: WorkflowState,
    role: str,
    status: str,
    session_id: str | None = None,
) -> None:
    callback = _runtime(state).get("session_status_callback")
    if callback is not None:
        callback(state["batch_index"], role, status, session_id)


def _set_mock_session_identity(client: Any, state: WorkflowState, role: str) -> None:
    configure = getattr(client, "set_session_identity", None)
    if callable(configure):
        runtime = _runtime(state)
        configure(
            run_id=runtime["run_id"],
            batch_index=state["batch_index"],
            role=f"voter_{role}" if role in {"A", "B", "C"} else role,
        )


@contextmanager
def _quiet_sqlglot_logs():
    global _SQLGLOT_LOG_LEVEL, _SQLGLOT_LOG_USERS
    logger = logging.getLogger("sqlglot")
    with _SQLGLOT_LOG_LOCK:
        if _SQLGLOT_LOG_USERS == 0:
            _SQLGLOT_LOG_LEVEL = logger.level
            logger.setLevel(logging.CRITICAL + 1)
        _SQLGLOT_LOG_USERS += 1
    try:
        yield
    finally:
        with _SQLGLOT_LOG_LOCK:
            _SQLGLOT_LOG_USERS -= 1
            if _SQLGLOT_LOG_USERS == 0:
                logger.setLevel(_SQLGLOT_LOG_LEVEL or logging.NOTSET)
                _SQLGLOT_LOG_LEVEL = None


def _obj_value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _all_names(objects: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    converted = {
        str(item.get("qualified_name", ""))
        for item in objects
        if not item.get("external") and item.get("qualified_name")
    }
    external = {
        str(item.get("qualified_name", ""))
        for item in objects
        if item.get("external") and item.get("qualified_name")
    }
    return converted, external


def _type_map(session: dict[str, Any], source: str, target: str) -> dict[str, list[str]]:
    from migraai.kb.search import get_mappings

    db_path = session.get("kb_path")
    try:
        source_mappings = get_mappings(source, db_path=db_path)
        target_mappings = get_mappings(target, db_path=db_path)
    except Exception:
        return {}
    targets: dict[str, list[str]] = {}
    for mapping in target_mappings:
        if mapping["kind"] == "type":
            targets.setdefault(str(mapping["canonical"]), []).append(str(mapping["native"]))
    return {
        str(mapping["native"]): targets.get(str(mapping["canonical"]), [])
        for mapping in source_mappings
        if mapping["kind"] == "type"
    }


def _source_grants(objects: list[dict[str, Any]]) -> dict[str, set[tuple[str, str]]]:
    from migraai.discovery import ast

    result: dict[str, set[tuple[str, str]]] = {}
    for item in objects:
        node = item.get("node")
        if not isinstance(node, ast.Node):
            continue
        for child in node.walk():
            if not isinstance(child, ast.Grant):
                continue
            grantees = [
                ".".join(part.value for part in grantee.parts) for grantee in child.grantees
            ]
            values = result.setdefault(str(item.get("source_qualified_name", "")), set())
            values.update(
                (grantee, privilege) for grantee in grantees for privilege in child.privileges
            )
    return result


def _validation_context(state: WorkflowState) -> ValidationContext:
    runtime = _runtime(state)
    source_objects = state["conversion_objects"]
    all_objects = runtime["all_objects"]
    converted, external = _all_names(all_objects)
    nameable_dependencies = {
        str(item.get("qualified_name", ""))
        for item in all_objects
        if not item.get("external")
        and is_nameable_object_type(str(item.get("type", "")).casefold())
    }
    type_map = _type_map(runtime["session"], state["source"], state["target"])
    table_columns: dict[str, list[tuple[str, str]]] = {}
    for item in source_objects:
        name = state["name_map"].get(item["source_qualified_name"], item["source_qualified_name"])
        columns = []
        for column in item.get("source_columns", []):
            source_type = str(column.get("type", "UNKNOWN"))
            targets = type_map.get(source_type, [])
            columns.append((str(column.get("name", "")), targets[0] if targets else source_type))
        table_columns[name] = columns
    return ValidationContext(
        target=state["target"],
        pack=runtime["pack"],
        name_map=state["name_map"],
        converted=converted,
        external=external,
        order=runtime["order"],
        nameable_dependencies=nameable_dependencies,
        table_columns=table_columns,
        type_map=type_map,
        source_grants=runtime["source_grants"],
    )


def _as_candidate(
    voter: str,
    object_id: str,
    value: Any,
    target: str,
    source_rel_path: str | None = None,
) -> Candidate | None:
    if not isinstance(value, dict):
        return None
    files = value.get("files")
    if not isinstance(files, list):
        return None
    normalized_files = [
        dict(file)
        for file in files
        if isinstance(file, dict)
        and isinstance(file.get("rel_path"), str)
        and isinstance(file.get("language"), str)
        and isinstance(file.get("content"), str)
    ]
    if not normalized_files:
        return None
    if len(normalized_files) == 1 and source_rel_path:
        normalized_files[0]["rel_path"] = source_rel_path
    try:
        confidence = float(value.get("self_confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return Candidate(
        voter=voter,
        object_id=object_id,
        files=normalized_files,
        explanations=list(value.get("explanations") or []),
        assumptions=list(value.get("assumptions") or []),
        unsupported=list(value.get("unsupported") or []),
        self_confidence=min(1.0, max(0.0, confidence)),
        normalized=normalise_files(normalized_files, target),
        raw=value,
    )


def _validation(
    source_object: dict[str, Any],
    candidate: Candidate,
    context: ValidationContext,
) -> list[Any]:
    output_files = [
        OutputFile(
            rel_path=str(item["rel_path"]),
            language=str(item["language"]),
            content=str(item["content"]),
            etl_format_id=item.get("etl_format_id"),
        )
        for item in candidate.files
    ]
    under_test = ObjectUnderTest(
        source_qualified_name=source_object["source_qualified_name"],
        object_type=source_object["object_type"],
        dependencies=source_object["dependencies"],
        source_column_types=[
            (str(column["name"]), str(column.get("type", "")))
            for column in source_object.get("source_columns", [])
        ],
        files=output_files,
    )
    return validate_object(under_test, context)


def _build_context(state: WorkflowState) -> dict[str, Any]:
    from migraai.agents.context import build_batch

    runtime = _runtime(state)
    attachment, objects = build_batch(
        runtime["session"],
        runtime["batch"],
        state["source"],
        state["target"],
        runtime["pack"],
        state["name_map"],
    )
    if secret_mask.contains_secret(attachment):
        raise secret_mask.SecretAttachmentError(
            "A possible secret was found in the code to send; nothing was sent for this batch."
        )
    runtime["source_grants"] = _source_grants(objects)
    for item in objects:
        item.pop("node", None)
    state["attachment_md"] = attachment
    state["conversion_objects"] = objects
    runtime["validation_context"] = _validation_context(state)
    return state


def _run_voter(state: WorkflowState, voter: str) -> tuple[str, SessionOutcome]:
    runtime = _runtime(state)
    if runtime["cancel"].is_set():
        outcome = SessionOutcome(None, None, "cancelled", None, None, "Session cancelled.")
        _update_session_progress(state, voter, outcome.state)
        return voter, outcome
    client = None
    prompt = VOTER_BASE.format(
        source=state["source"],
        target=state["target"],
        count=len(state["conversion_objects"]),
        stance=STANCES[voter].format(target=state["target"]),
    )
    try:
        client = runtime["client_factory"](voter)
        _set_mock_session_identity(client, state, voter)
        with runtime["session_semaphore"]:
            outcome = run_session(
                client,
                prompt=prompt,
                attachment_name=f"conversion-batch-{state['batch_index'] + 1}.md",
                attachment_md=state["attachment_md"],
                schema=CONVERSION_SCHEMA,
                title=f"{state['source']} to {state['target']} conversion batch",
                tags=[
                    "migraai",
                    f"batch-{state['batch_index'] + 1}",
                    f"voter-{voter}",
                ],
                timeout_s=runtime["timeout_s"],
                cancel=runtime["cancel"],
                poll_initial=0.1,
                poll_max=3,
                on_session_started=lambda session_id: _update_session_progress(
                    state, voter, "running", session_id
                ),
            )
    except Exception as error:
        outcome = SessionOutcome(
            None,
            None,
            "failed",
            None,
            None,
            f"Conversion voter failed ({type(error).__name__}).",
        )
    finally:
        close = getattr(client, "close", None) if client is not None else None
        if close is not None:
            try:
                close()
            except Exception:
                pass
    _update_session_progress(state, voter, outcome.state, outcome.session_id)
    return voter, outcome


def _vote(state: WorkflowState) -> dict[str, Any]:
    outcomes: dict[str, SessionOutcome] = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(_run_voter, state, voter) for voter in ("A", "B", "C")]
        for future in as_completed(futures):
            voter, outcome = future.result()
            outcomes[voter] = outcome
    candidates: dict[str, dict[str, Candidate | None]] = {}
    object_errors = dict(state.get("object_errors", {}))
    for source_object in state["conversion_objects"]:
        object_id = source_object["object_id"]
        candidates[object_id] = {}
        try:
            for voter, outcome in outcomes.items():
                output = outcome.structured_output
                entries = output.get("objects", []) if isinstance(output, dict) else []
                entry = next(
                    (
                        item
                        for item in entries
                        if isinstance(item, dict) and item.get("object_id") == object_id
                    ),
                    None,
                )
                candidates[object_id][voter] = _as_candidate(
                    voter,
                    object_id,
                    entry,
                    state["target"],
                    str(source_object.get("rel_path", "")),
                )
        except Exception as error:
            object_errors[object_id] = _processing_error("Candidate normalization", error)
            candidates[object_id] = {voter: None for voter in ("A", "B", "C")}
    return {
        "voter_outcomes": outcomes,
        "candidates": candidates,
        "object_errors": object_errors,
    }


def _consensus(state: WorkflowState) -> dict[str, Any]:
    chosen: dict[str, Any] = {}
    object_errors = dict(state.get("object_errors", {}))
    for source_object in state["conversion_objects"]:
        object_id = source_object["object_id"]
        if object_id in object_errors:
            chosen[object_id] = {
                "candidate": None,
                "picked_voter": None,
                "agreement": None,
                "pairwise": {},
            }
            continue
        try:
            result = choose(state["candidates"][object_id], target=state["target"])
        except Exception as error:
            object_errors[object_id] = _processing_error("Consensus selection", error)
            chosen[object_id] = {
                "candidate": None,
                "picked_voter": None,
                "agreement": None,
                "pairwise": {},
            }
            continue
        chosen[object_id] = {
            "candidate": (
                state["candidates"][object_id].get(result.picked_voter)
                if result.picked_voter
                else None
            ),
            "picked_voter": result.picked_voter,
            "agreement": result.agreement,
            "pairwise": result.pairwise,
        }
    return {"consensus": chosen, "object_errors": object_errors}


def _validate(state: WorkflowState) -> dict[str, Any]:
    candidate_validations: dict[str, dict[str, list[Any]]] = {}
    picked_validations: dict[str, list[Any]] = {}
    object_errors = dict(state.get("object_errors", {}))
    for source_object in state["conversion_objects"]:
        object_id = source_object["object_id"]
        candidates = state["candidates"][object_id]
        selected = state["consensus"][object_id]
        if object_id in object_errors:
            candidate_validations[object_id] = {}
            picked_validations[object_id] = []
            selected.update(candidate=None, picked_voter=None, agreement=None, pairwise={})
            continue
        try:
            results = {
                voter: _validation(source_object, candidate, _runtime(state)["validation_context"])
                for voter, candidate in candidates.items()
                if candidate is not None
            }
            result = choose(candidates, results, target=state["target"])
        except Exception as error:
            object_errors[object_id] = _processing_error("Validation", error)
            candidate_validations[object_id] = {}
            picked_validations[object_id] = []
            selected.update(candidate=None, picked_voter=None, agreement=None, pairwise={})
            continue
        candidate_validations[object_id] = results
        selected.update(
            candidate=candidates.get(result.picked_voter) if result.picked_voter else None,
            picked_voter=result.picked_voter,
            agreement=result.agreement,
            pairwise=result.pairwise,
        )
        picked_validations[object_id] = (
            results.get(result.picked_voter, []) if result.picked_voter else []
        )
    return {
        "candidate_validations": candidate_validations,
        "picked_validations": picked_validations,
        "object_errors": object_errors,
    }


def _score(state: WorkflowState) -> dict[str, Any]:
    scores: dict[str, float] = {}
    to_escalate: list[str] = []
    object_errors = dict(state.get("object_errors", {}))
    for source_object in state["conversion_objects"]:
        object_id = source_object["object_id"]
        selected = state["consensus"][object_id]
        if object_id in object_errors:
            selected["candidate"] = None
            scores[object_id] = 0.0
            continue
        candidate = selected["candidate"]
        try:
            value = (
                score(
                    selected["agreement"],
                    pass_rate(state["picked_validations"][object_id]),
                    [
                        item.self_confidence
                        for item in state["candidates"][object_id].values()
                        if item is not None
                    ],
                )
                if candidate is not None
                else 0.0
            )
        except Exception as error:
            object_errors[object_id] = _processing_error("Confidence scoring", error)
            selected["candidate"] = None
            scores[object_id] = 0.0
            continue
        scores[object_id] = value
        if value < state["confidence_threshold"]:
            to_escalate.append(object_id)
    return {"scores": scores, "to_escalate": to_escalate, "object_errors": object_errors}


def _run_escalation(state: WorkflowState) -> dict[str, Any]:
    runtime = _runtime(state)
    if not state["to_escalate"]:
        return {"escalation_outcome": None, "escalation_candidates": {}, "escalated": False}
    candidates = state["candidates"]
    context_by_id = {item["object_id"]: item for item in state["conversion_objects"]}
    escalation_objects = []
    for object_id in state["to_escalate"]:
        source_object = context_by_id[object_id]
        candidate_summary = {}
        findings = {}
        for voter, candidate in candidates[object_id].items():
            candidate_summary[voter] = candidate.raw if candidate else None
            findings[voter] = [
                asdict(finding)
                for finding in state["candidate_validations"][object_id].get(voter, [])
            ]
        escalation_objects.append(
            {
                "object_id": object_id,
                "rel_path": source_object["rel_path"],
                "language": source_object["language"],
                "object_type": source_object["object_type"],
                "candidates": candidate_summary,
                "validator_findings": findings,
                "source_text": source_object["source_text"],
            }
        )
    attachment = render_batch(
        escalation_objects,
        sections=[
            (
                "Original batch context",
                state["attachment_md"].split("<!-- migraai-object -->", 1)[0].strip(),
            )
        ],
    )
    client = None
    try:
        client = runtime["client_factory"]("escalation")
        _set_mock_session_identity(client, state, "escalation")
        with runtime["session_semaphore"]:
            outcome = run_session(
                client,
                prompt=ESCALATION.format(
                    source=state["source"],
                    target=state["target"],
                    count=len(escalation_objects),
                ),
                attachment_name=f"conversion-batch-{state['batch_index'] + 1}-escalation.md",
                attachment_md=attachment,
                schema=CONVERSION_SCHEMA,
                title=f"{state['source']} to {state['target']} conversion escalation",
                tags=["migraai", f"batch-{state['batch_index'] + 1}", "escalation"],
                timeout_s=runtime["timeout_s"],
                cancel=runtime["cancel"],
                poll_initial=0.1,
                poll_max=3,
                on_session_started=lambda session_id: _update_session_progress(
                    state, "escalation", "running", session_id
                ),
            )
    except Exception as error:
        outcome = SessionOutcome(
            None,
            None,
            "failed",
            None,
            None,
            f"Conversion escalation failed ({type(error).__name__}).",
        )
    finally:
        close = getattr(client, "close", None) if client is not None else None
        if close is not None:
            try:
                close()
            except Exception:
                pass
    _update_session_progress(state, "escalation", outcome.state, outcome.session_id)
    revised: dict[str, Candidate | None] = {}
    output = outcome.structured_output
    entries = output.get("objects", []) if isinstance(output, dict) else []
    source_rel_paths = {
        item["object_id"]: str(item.get("rel_path", "")) for item in state["conversion_objects"]
    }
    object_errors = dict(state.get("object_errors", {}))
    for object_id in state["to_escalate"]:
        entry = next(
            (
                item
                for item in entries
                if isinstance(item, dict) and item.get("object_id") == object_id
            ),
            None,
        )
        try:
            revised[object_id] = (
                _as_candidate(
                    "escalation",
                    object_id,
                    entry,
                    state["target"],
                    source_rel_paths.get(object_id),
                )
                if outcome.state == "done"
                else None
            )
        except Exception as error:
            object_errors[object_id] = _processing_error("Escalation normalization", error)
            revised[object_id] = None
    return {
        "escalation_outcome": outcome,
        "escalation_candidates": revised,
        "escalated": outcome.state == "done" and any(revised.values()),
        "object_errors": object_errors,
    }


def _revalidate(state: WorkflowState) -> dict[str, Any]:
    results = {key: dict(value) for key, value in state["consensus"].items()}
    picked_validations = dict(state["picked_validations"])
    scores = dict(state["scores"])
    object_errors = dict(state.get("object_errors", {}))
    for object_id in state["to_escalate"]:
        if object_id in object_errors:
            continue
        revised = state.get("escalation_candidates", {}).get(object_id)
        if revised is None:
            continue
        source_object = next(
            item for item in state["conversion_objects"] if item["object_id"] == object_id
        )
        try:
            checks = _validation(source_object, revised, _runtime(state)["validation_context"])
            previous = results[object_id]
            results[object_id] = {
                "candidate": revised,
                "picked_voter": "escalation",
                "agreement": previous["agreement"],
                "pairwise": previous["pairwise"],
            }
            picked_validations[object_id] = checks
            confidences = [
                item.self_confidence
                for item in state["candidates"][object_id].values()
                if item is not None
            ]
            confidences.append(revised.self_confidence)
            scores[object_id] = score(
                previous["agreement"],
                pass_rate(checks),
                confidences,
            )
        except Exception as error:
            object_errors[object_id] = _processing_error("Revalidation", error)
            results[object_id]["candidate"] = None
            scores[object_id] = 0.0
    return {
        "consensus": results,
        "picked_validations": picked_validations,
        "scores": scores,
        "object_errors": object_errors,
    }


def _failure_reason(error: str | None) -> str:
    if not error:
        return ""
    return " ".join(secret_mask.mask_secrets(str(error)).split())[:200]


def _processing_error(stage: str, error: Exception) -> str:
    return _failure_reason(f"{stage} failed ({type(error).__name__}): {error}")


def _session_failure_description(
    outcome: SessionOutcome | None,
    candidate: Candidate | None,
) -> str:
    if outcome is None:
        return "session unknown"
    if outcome.state == "done" and candidate is None:
        return "returned no output for this object"
    description = f"session {outcome.state}"
    error = _failure_reason(outcome.error)
    if error:
        description += f": {error}"
    return description


def _finalise(state: WorkflowState) -> dict[str, Any]:
    results = {}
    for source_object in state["conversion_objects"]:
        object_id = source_object["object_id"]
        selected = state["consensus"][object_id]
        object_error = state.get("object_errors", {}).get(object_id)
        candidate = None if object_error else selected["candidate"]
        confidence = state["scores"][object_id]
        if candidate is None:
            status = "failed"
        elif confidence >= state["confidence_threshold"]:
            status = "converted"
        else:
            status = "expert_review"
        result = {
            "object_id": object_id,
            "source_qualified_name": source_object["source_qualified_name"],
            "status": status,
            "confidence": confidence,
            "picked_voter": selected["picked_voter"],
            "pairwise": selected["pairwise"],
            "candidate": candidate,
            "checks": state["picked_validations"].get(object_id, []),
            "escalated": object_id in state["to_escalate"],
            "agreement": selected["agreement"],
        }
        if candidate is None:
            if object_error:
                result["reason"] = f"Object processing failed: {_failure_reason(object_error)}"
            else:
                candidates = state["candidates"].get(object_id, {})
                outcomes = state.get("voter_outcomes", {})
                reasons = [
                    f"{voter} "
                    f"{_session_failure_description(outcomes.get(voter), candidates.get(voter))}"
                    for voter in ("A", "B", "C")
                ]
                escalation = state.get("escalation_outcome")
                if escalation is not None:
                    escalation_candidate = state.get("escalation_candidates", {}).get(object_id)
                    reasons.append(
                        "escalation "
                        + _session_failure_description(escalation, escalation_candidate)
                    )
                result["reason"] = "No usable candidate: " + "; ".join(reasons)
        results[object_id] = result
    return {"results": results}


def build_workflow():
    graph = StateGraph(WorkflowState)
    graph.add_node("build_context", _build_context)
    graph.add_node("vote", _vote)
    graph.add_node("consensus", _consensus)
    graph.add_node("validate", _validate)
    graph.add_node("score", _score)
    graph.add_node("escalate", _run_escalation)
    graph.add_node("revalidate", _revalidate)
    graph.add_node("finalise", _finalise)
    graph.add_edge(START, "build_context")
    graph.add_edge("build_context", "vote")
    graph.add_edge("vote", "consensus")
    graph.add_edge("consensus", "validate")
    graph.add_edge("validate", "score")
    graph.add_edge("score", "escalate")
    graph.add_edge("escalate", "revalidate")
    graph.add_edge("revalidate", "finalise")
    graph.add_edge("finalise", END)
    return graph.compile(checkpointer=MemorySaver())


def run_batch_workflow(
    *,
    session: dict[str, Any],
    batch: Any,
    source: str,
    target: str,
    pack: Any,
    name_map: dict[str, str],
    all_objects: list[dict[str, Any]],
    all_edges: list[Any],
    order: list[str],
    confidence_threshold: float,
    timeout_s: float,
    cancel: threading.Event,
    session_semaphore: threading.Semaphore,
    client_factory: Callable[[str], Any],
    session_status_callback: Callable[[int, str, str, str | None], None] | None = None,
    workflow: Any | None = None,
    run_id: str = "conversion",
) -> WorkflowState:
    graph = workflow or build_workflow()
    run_key = f"{run_id}-{batch.index}"
    runtime = {
        "session": session,
        "batch": batch,
        "pack": pack,
        "all_objects": all_objects,
        "all_edges": all_edges,
        "order": order,
        "timeout_s": timeout_s,
        "cancel": cancel,
        "session_semaphore": session_semaphore,
        "client_factory": client_factory,
        "run_id": run_id,
        "session_status_callback": session_status_callback,
    }
    with _RUNTIME_LOCK:
        _RUNTIME_CONTEXTS[run_key] = runtime
    state: WorkflowState = {
        "run_key": run_key,
        "batch_index": batch.index,
        "source": source,
        "target": target,
        "name_map": name_map,
        "confidence_threshold": confidence_threshold,
    }
    try:
        with _quiet_sqlglot_logs():
            return graph.invoke(
                state,
                config={"configurable": {"thread_id": run_key}},
            )
    finally:
        with _RUNTIME_LOCK:
            _RUNTIME_CONTEXTS.pop(run_key, None)


__all__ = ["CONVERSION_SCHEMA", "WorkflowState", "build_workflow", "run_batch_workflow"]
