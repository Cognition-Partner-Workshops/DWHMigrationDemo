from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from migraai.agents.batching import plan_batches
from migraai.agents.naming import build_name_map
from migraai.agents.workflow import build_workflow, run_batch_workflow
from migraai.core.secret_mask import SecretAttachmentError
from migraai.devin.client import build_devin_client
from migraai.platforms.pack import TargetPack, load_pack
from migraai.voting.confidence import overall_score

_TERMINAL_SESSION_STATES = {"done", "failed", "blocked", "timeout", "cancelled"}


class ConversionJob(threading.Thread):
    def __init__(
        self,
        *,
        session: dict[str, Any],
        audit: Any,
        devin_pat: str = "",
        org_id: str = "",
        base_url: str = "https://api.devin.ai",
        batch_size: int = 20,
        timeout_minutes: int = 45,
        max_concurrent_sessions: int = 4,
        confidence_threshold: float = 0.85,
        devin_host: str = "",
        client_factory: Callable[[str], Any] | None = None,
        pack: TargetPack | None = None,
        consent: bool = True,
        object_ids: list[str] | None = None,
        base: ConversionJob | None = None,
        replayed: bool = False,
    ) -> None:
        super().__init__(daemon=True)
        self.session = session
        self.audit = audit
        self.source = session["source"]
        self.target = session["target"]
        self.run_id = str(uuid.uuid4())
        self.batch_size = batch_size
        self.timeout_minutes = timeout_minutes
        self.max_concurrent_sessions = max_concurrent_sessions
        self.confidence_threshold = confidence_threshold
        self.devin_host = devin_host
        self.pack = pack or load_pack(self.target)
        self.base = base
        self.base_run_id = base.run_id if base is not None else None
        self.replayed = replayed
        self.client_factory = client_factory or (
            lambda voter: build_devin_client(base_url, org_id, devin_pat)
        )
        self.cancel_event = threading.Event()
        self.lock = threading.RLock()
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.started_at = time.monotonic()
        self.state = "queued"
        self.reason: str | None = None
        self.results: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self.started_sessions: dict[str, dict[str, Any]] = {}
        self.counts = {
            "converted": 0,
            "escalated": 0,
            "expert_review": 0,
            "failed": 0,
        }
        self.all_objects = self._inventory_objects()
        self.objects = [
            item for item in self.all_objects if not self._value(item, "external", False)
        ]
        self.edges = self._inventory_edges()
        requested_ids = (
            set(object_ids)
            if object_ids is not None
            else {str(self._value(item, "id", "")) for item in self.objects}
        )
        self.selected_objects = [
            item for item in self.objects if str(self._value(item, "id", "")) in requested_ids
        ]
        self.all_batches = plan_batches(self.all_objects, self.edges, batch_size)
        self.batches = (
            self.all_batches
            if object_ids is None
            else plan_batches(self.selected_objects, self.edges, batch_size)
        )
        self.reconverted_object_ids = (
            tuple(object_id for batch in self.batches for object_id in batch.object_ids)
            if base is not None
            else ()
        )
        self.name_map = build_name_map(self.all_objects, self.pack)
        self.order = [
            str(self._value(item, "qualified_name", ""))
            for batch in self.all_batches
            for item in batch.objects
        ]
        if base is not None:
            selected_ids = set(self.reconverted_object_ids)
            for item in self.objects:
                object_id = str(self._value(item, "id", ""))
                if object_id in selected_ids:
                    continue
                result = base.results.get(object_id)
                if result is None:
                    result = self._unresolved_base_result(base, object_id)
                self.results[object_id] = result
                if object_id in base.errors:
                    self.errors[object_id] = base.errors[object_id]
                status = str(result.get("status", "failed"))
                if status in {"converted", "expert_review", "failed"}:
                    self.counts[status] += 1
                elif status == "cancelled":
                    self.counts.setdefault("cancelled", 0)
                    self.counts["cancelled"] += 1
                self.counts["escalated"] += int(bool(result.get("escalated")))
        self.batch_status = {
            batch.index: {
                "status": "queued",
                "objects": [str(self._value(item, "id", "")) for item in batch.objects],
                "voters": {
                    "A": "queued",
                    "B": "queued",
                    "C": "queued",
                    "escalation": "not_needed",
                },
                "escalated": False,
            }
            for batch in self.batches
        }
        self._create_tables()
        if consent:
            self._audit(
                "convert.consent",
                {
                    "object_count": len(self.selected_objects),
                    "base_run_id": self.base_run_id,
                },
            )

    @staticmethod
    def _unresolved_base_result(base: ConversionJob, object_id: str) -> dict[str, Any]:
        batch_state = next(
            (
                base.batch_status[batch.index]["status"]
                for batch in base.batches
                if object_id in batch.object_ids
            ),
            "",
        )
        status = (
            "cancelled" if base.state == "cancelled" or batch_state == "cancelled" else "failed"
        )
        return {
            "status": status,
            "confidence": None,
            "agreement": None,
            "checks": [],
            "candidate": None,
            "picked_voter": None,
            "pairwise": {},
            "escalated": False,
            "reason": base.errors.get(object_id),
        }

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)

    def _inventory_objects(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            current = self.session["inventory"].page(page=page, page_size=300)["items"]
            items.extend(current)
            if len(current) < 300:
                return items
            page += 1

    def _inventory_edges(self) -> list[dict[str, Any]]:
        inventory = self.session["inventory"]
        with inventory.lock:
            return [
                dict(row)
                for row in inventory.connection.execute(
                    "SELECT source_id, target_id, kind, external FROM dependency"
                )
            ]

    def _create_tables(self) -> None:
        inventory = self.session["inventory"]
        with inventory.lock, inventory.connection:
            inventory.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversion_run (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,
                    state TEXT NOT NULL, object_count INTEGER NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversion_object (
                    run_id TEXT NOT NULL, object_id TEXT NOT NULL, state TEXT NOT NULL,
                    confidence REAL, picked_voter TEXT, code_path TEXT,
                    PRIMARY KEY(run_id, object_id)
                );
                CREATE TABLE IF NOT EXISTS conversion_candidate (
                    run_id TEXT NOT NULL, object_id TEXT NOT NULL, voter TEXT NOT NULL,
                    state TEXT NOT NULL, self_confidence REAL, code_path TEXT,
                    session_id TEXT, acus REAL,
                    PRIMARY KEY(run_id, object_id, voter)
                );
                CREATE TABLE IF NOT EXISTS conversion_session (
                    run_id TEXT NOT NULL, object_id TEXT NOT NULL, role TEXT NOT NULL,
                    session_id TEXT, url TEXT, state TEXT NOT NULL, acus REAL, error TEXT
                );
                """
            )
            inventory.connection.execute(
                "INSERT INTO conversion_run VALUES(?,?,?,?,?,?)",
                (
                    self.run_id,
                    self.source,
                    self.target,
                    "queued",
                    len(self.selected_objects),
                    time.time(),
                ),
            )

    def _audit(self, action: str, details: dict[str, Any] | None = None) -> None:
        if self.audit is not None:
            if action == "convert.consent" and self.devin_host:
                details = {**(details or {}), "devin_host": self.devin_host}
            self.audit.append("user", action, self.run_id, details or {})

    def _publish(self, event: str, data: dict[str, Any]) -> None:
        payload = {"event": event, "data": data}
        self.events.put(payload)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            batches = {
                index: {
                    **batch,
                    "objects": list(batch["objects"]),
                    "voters": dict(batch["voters"]),
                }
                for index, batch in self.batch_status.items()
            }
            return {
                "run_id": self.run_id,
                "state": self.state,
                "progress": {
                    "elapsed_s": round(time.monotonic() - self.started_at, 1),
                    "batches": batches,
                },
                "counts": dict(self.counts),
                "overall_confidence": overall_score(
                    (float(item.get("confidence") or 0.0) for item in self.results.values()),
                    (1 for _ in self.results),
                ),
                "object_count": len(self.selected_objects),
                "batch_count": len(self.batches),
                "reason": self.reason,
                "base_run_id": self.base_run_id,
                "reconverted_object_ids": list(self.reconverted_object_ids),
            }

    def _update_session_status(
        self,
        batch_index: int,
        role: str,
        status: str,
        session_id: str | None = None,
    ) -> None:
        with self.lock:
            session_state = status
            if self.cancel_event.is_set() and status not in _TERMINAL_SESSION_STATES:
                session_state = "cancelled"
            if session_id:
                started = self.started_sessions.setdefault(
                    session_id,
                    {
                        "role": role,
                        "batch_index": batch_index,
                        "state": session_state,
                        "url": None,
                        "acus": None,
                        "run_id": self.run_id,
                        "replayed": self.replayed,
                    },
                )
                started["role"] = role
                started["batch_index"] = batch_index
                if not (started.get("state") == "cancelled" and session_state != "cancelled"):
                    started["state"] = session_state
                session_state = str(started["state"])
            batch = self.batch_status[batch_index]
            if batch["voters"].get(role) == session_state:
                return
            batch["voters"][role] = session_state
            if role == "escalation" and session_state not in {"queued", "not_needed"}:
                batch["escalated"] = True
            event_data = {
                "batch_index": batch_index,
                "object_ids": list(batch["objects"]),
                "voter": role,
                "state": session_state,
                "session_id": session_id,
                "voters": dict(batch["voters"]),
                "escalated": batch["escalated"],
            }
        self._publish("convert.batch", event_data)

    def event_stream(self) -> Iterator[str]:
        while True:
            try:
                event = self.events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                return
            data = json.dumps(event["data"], separators=(",", ":"))
            yield f"event: {event['event']}\ndata: {data}\n\n"

    def cancel(self) -> None:
        self.cancel_event.set()
        progress_events: list[dict[str, Any]] = []
        with self.lock:
            self.state = "cancelled"
            for batch in self.batch_status.values():
                if batch["status"] == "queued":
                    batch["status"] = "cancelled"
            for session_id, started in self.started_sessions.items():
                if started.get("state") in _TERMINAL_SESSION_STATES:
                    continue
                started["state"] = "cancelled"
                batch_index = int(started["batch_index"])
                role = str(started["role"])
                batch = self.batch_status[batch_index]
                if batch["voters"].get(role) != "cancelled":
                    batch["voters"][role] = "cancelled"
                    progress_events.append(
                        {
                            "batch_index": batch_index,
                            "object_ids": list(batch["objects"]),
                            "voter": role,
                            "state": "cancelled",
                            "session_id": session_id,
                            "voters": dict(batch["voters"]),
                            "escalated": batch["escalated"],
                        }
                    )
        for event_data in progress_events:
            self._publish("convert.batch", event_data)
        self._audit("convert.cancel", {"completed_count": len(self.results)})
        self._publish("convert.cancel", {"run_id": self.run_id})
        self._persist_state("cancelled")

    def _persist_state(self, state: str) -> None:
        inventory = self.session["inventory"]
        with inventory.lock, inventory.connection:
            inventory.connection.execute(
                "UPDATE conversion_run SET state=? WHERE id=?", (state, self.run_id)
            )

    def _store_json(self, rel_path: str, value: Any) -> str:
        self.session["store"].write(
            rel_path,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )
        return rel_path

    def _save_batch(self, batch: Any, result: Any) -> None:
        workflow_results = result.get("results", {})
        outcome_by_voter = result.get("voter_outcomes", {})
        inventory = self.session["inventory"]
        with self.lock, inventory.lock, inventory.connection:
            for voter, outcome in outcome_by_voter.items():
                if outcome.session_id:
                    started = self.started_sessions.get(outcome.session_id)
                    if started is not None:
                        started["state"] = outcome.state
                        started["url"] = outcome.url
                        started["acus"] = outcome.acus
                for object_id, candidates in result.get("candidates", {}).items():
                    candidate = candidates.get(voter)
                    path = None
                    state = "failed"
                    confidence = None
                    if candidate is not None:
                        path = self._store_json(
                            f"conversion/{self.run_id}/{object_id}/{voter}.json",
                            candidate.files,
                        )
                        state = "done"
                        confidence = candidate.self_confidence
                    inventory.connection.execute(
                        """INSERT OR REPLACE INTO conversion_candidate
                        (run_id,object_id,voter,state,self_confidence,code_path,session_id,acus)
                        VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            self.run_id,
                            object_id,
                            voter,
                            state,
                            confidence,
                            path,
                            outcome.session_id,
                            outcome.acus,
                        ),
                    )
                    inventory.connection.execute(
                        """INSERT INTO conversion_session
                        (run_id,object_id,role,session_id,url,state,acus,error)
                        VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            self.run_id,
                            object_id,
                            voter,
                            outcome.session_id,
                            outcome.url,
                            outcome.state,
                            outcome.acus,
                            outcome.error,
                        ),
                    )
            for object_id, item in workflow_results.items():
                candidate = item.get("candidate")
                path = None
                if candidate is not None:
                    path = self._store_json(
                        f"conversion/{self.run_id}/{object_id}/final.json",
                        candidate.files,
                    )
                inventory.connection.execute(
                    """INSERT OR REPLACE INTO conversion_object
                    (run_id,object_id,state,confidence,picked_voter,code_path)
                    VALUES(?,?,?,?,?,?)""",
                    (
                        self.run_id,
                        object_id,
                        item["status"],
                        item["confidence"],
                        item["picked_voter"],
                        path,
                    ),
                )
                self.results[object_id] = item
                if (
                    item["status"] == "failed"
                    and item.get("reason")
                    and object_id not in self.errors
                ):
                    self.errors[object_id] = str(item["reason"])
                self.counts[item["status"]] += 1
            batch_status = self.batch_status[batch.index]
            batch_status["status"] = "done"
            batch_status["voters"] = {
                voter: outcome.state for voter, outcome in outcome_by_voter.items()
            }
            escalation = result.get("escalation_outcome")
            if escalation is not None and escalation.session_id:
                started = self.started_sessions.get(escalation.session_id)
                if started is not None:
                    started["state"] = escalation.state
                    started["url"] = escalation.url
                    started["acus"] = escalation.acus
            batch_status["voters"]["escalation"] = (
                escalation.state if escalation is not None else "not_needed"
            )
            batch_status["escalated"] = escalation is not None
            self.counts["escalated"] += sum(
                bool(item.get("escalated")) for item in workflow_results.values()
            )
        sessions = [
            outcome.session_id for outcome in outcome_by_voter.values() if outcome.session_id
        ]
        acus = [outcome.acus for outcome in outcome_by_voter.values() if outcome.acus is not None]
        escalation = result.get("escalation_outcome")
        if escalation is not None:
            if escalation.session_id:
                sessions.append(escalation.session_id)
            if escalation.acus is not None:
                acus.append(escalation.acus)
        batch_ids = [str(self._value(item, "id", "")) for item in batch.objects]
        self._audit(
            "convert.batch",
            {"object_ids": batch_ids, "session_ids": sessions, "acus": acus},
        )
        self._publish(
            "convert.batch",
            {
                "batch_index": batch.index,
                "object_ids": batch_ids,
                "session_ids": sessions,
                "counts": dict(self.counts),
            },
        )
        escalation = result.get("escalation_outcome")
        if escalation is not None:
            for object_id, item in workflow_results.items():
                if not item.get("escalated"):
                    continue
                with inventory.lock, inventory.connection:
                    inventory.connection.execute(
                        """INSERT INTO conversion_session
                        (run_id,object_id,role,session_id,url,state,acus,error)
                        VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            self.run_id,
                            object_id,
                            "escalation",
                            escalation.session_id,
                            escalation.url,
                            escalation.state,
                            escalation.acus,
                            escalation.error,
                        ),
                    )
            escalation_ids = [
                object_id for object_id, item in workflow_results.items() if item.get("escalated")
            ]
            self._audit(
                "convert.escalation",
                {
                    "object_ids": escalation_ids,
                    "session_id": escalation.session_id if escalation else None,
                },
            )
            self._publish(
                "convert.escalation",
                {
                    "batch_index": batch.index,
                    "object_ids": escalation_ids,
                    "session_id": escalation.session_id if escalation else None,
                },
            )

    def _save_failed_batch(self, batch: Any, error: str | None = None) -> None:
        inventory = self.session["inventory"]
        with self.lock, inventory.lock, inventory.connection:
            for item in batch.objects:
                object_id = str(self._value(item, "id", ""))
                inventory.connection.execute(
                    """INSERT OR REPLACE INTO conversion_object
                    (run_id,object_id,state,confidence,picked_voter,code_path)
                    VALUES(?,?,?,NULL,NULL,NULL)""",
                    (self.run_id, object_id, "failed"),
                )
                self.counts["failed"] += 1
                if error:
                    self.errors[object_id] = error
                self.results[object_id] = {
                    "status": "failed",
                    "confidence": None,
                    "agreement": None,
                    "checks": [],
                    "candidate": None,
                    "picked_voter": None,
                    "pairwise": {},
                    "escalated": False,
                    "reason": error,
                }
            self.batch_status[batch.index]["status"] = "failed"
            if error:
                self.batch_status[batch.index]["error"] = error

    def run(self) -> None:
        self.state = "running"
        self._persist_state("running")
        self._audit(
            "convert.start",
            {"object_count": len(self.selected_objects), "batch_count": len(self.batches)},
        )
        self._publish("convert.start", {"run_id": self.run_id, "batch_count": len(self.batches)})
        semaphore = threading.BoundedSemaphore(self.max_concurrent_sessions)
        workflow = build_workflow()
        try:
            with ThreadPoolExecutor(max_workers=max(1, self.max_concurrent_sessions)) as executor:
                futures = {}
                for batch in self.batches:
                    if self.cancel_event.is_set():
                        break
                    with self.lock:
                        self.batch_status[batch.index]["status"] = "running"
                    future = executor.submit(
                        run_batch_workflow,
                        session=self.session,
                        batch=batch,
                        source=self.source,
                        target=self.target,
                        pack=self.pack,
                        name_map=self.name_map,
                        all_objects=self.all_objects,
                        all_edges=self.edges,
                        order=self.order,
                        confidence_threshold=self.confidence_threshold,
                        timeout_s=self.timeout_minutes * 60,
                        cancel=self.cancel_event,
                        session_semaphore=semaphore,
                        client_factory=self.client_factory,
                        session_status_callback=self._update_session_status,
                        workflow=workflow,
                        run_id=self.run_id,
                    )
                    futures[future] = batch
                for future in as_completed(futures):
                    batch = futures[future]
                    if self.cancel_event.is_set():
                        continue
                    try:
                        self._save_batch(batch, future.result())
                    except Exception as error:
                        if isinstance(error, SecretAttachmentError):
                            message = str(error)
                            self.reason = message
                            object_ids = [
                                str(self._value(item, "id", "")) for item in batch.objects
                            ]
                            self._audit(
                                "convert.blocked_secret",
                                {"batch_index": batch.index, "object_ids": object_ids},
                            )
                            self._save_failed_batch(batch, message)
                        else:
                            self.reason = type(error).__name__
                            self._save_failed_batch(batch)
            if self.cancel_event.is_set():
                self.state = "cancelled"
                for batch in self.batches:
                    if self.batch_status[batch.index]["status"] in {"queued", "running"}:
                        self.batch_status[batch.index]["status"] = "cancelled"
            else:
                self.state = "done" if not self.reason else "failed"
            self._mark_unfinished_results()
        except Exception as error:
            self.reason = type(error).__name__
            self.state = "failed"
            self._mark_unfinished_results()
        self._persist_state(self.state)
        self._audit("convert.complete", {"counts": dict(self.counts), "state": self.state})
        self._publish(
            "convert.complete",
            {"run_id": self.run_id, "counts": dict(self.counts), "state": self.state},
        )
        self.events.put(None)

    def _mark_unfinished_results(self) -> None:
        status = "cancelled" if self.state == "cancelled" else "failed"
        for item in self.selected_objects:
            object_id = str(self._value(item, "id", ""))
            if object_id in self.results:
                continue
            self.results[object_id] = {
                "status": status,
                "confidence": None,
                "agreement": None,
                "checks": [],
                "candidate": None,
                "picked_voter": None,
                "pairwise": {},
                "escalated": False,
                "reason": self.reason,
            }
            if status == "cancelled":
                self.counts.setdefault("cancelled", 0)
                self.counts["cancelled"] += 1
            else:
                self.counts["failed"] += 1


__all__ = ["ConversionJob"]
