from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit

from migraai.core.audit import AuditLog
from migraai.devin.client import DevinClient, DevinV1Client, build_devin_client
from migraai.kb.store import KBStore
from migraai.planning.context import PlanContext, build_context
from migraai.planning.local_draft import build_local_draft
from migraai.planning.prompt import build_prompt
from migraai.planning.schema import SCHEMA, Plan
from migraai.planning.store import PlanStore


class PlanJob(threading.Thread):
    def __init__(
        self,
        *,
        source: str,
        target: str,
        db_path,
        kb_store: KBStore,
        plan_store: PlanStore,
        audit: AuditLog,
        base_url: str,
        org_id: str,
        pat: str,
        acu_cap: int = 10,
        timeout_minutes: int = 30,
        client_factory=None,
        inventory_aggregates: dict | None = None,
        inventory_fingerprint: str = "",
    ) -> None:
        super().__init__(daemon=True)
        self.source = source
        self.target = target
        self.db_path = db_path
        self.kb_store = kb_store
        self.plan_store = plan_store
        self.audit = audit
        self.base_url = base_url
        self.org_id = org_id
        self.pat = pat
        self.acu_cap = acu_cap
        self.timeout_minutes = timeout_minutes
        self.client_factory = client_factory
        self.inventory_aggregates = inventory_aggregates
        self.inventory_fingerprint = inventory_fingerprint
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self._termination_lock = threading.Lock()
        self._termination_attempted = False
        self.state = "queued"
        self.phase = "queued"
        self.started = time.monotonic()
        self.devin_url: str | None = None
        self.reason: str | None = None
        self.plan: dict | None = None
        self.record: dict | None = None
        self.session_id: str | None = None
        self.client = None
        self.origin: str | None = None
        self.cached = False

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "state": self.state,
                "progress": {
                    "phase": self.phase,
                    "elapsed_s": round(time.monotonic() - self.started, 1),
                    "devin_url": self.devin_url,
                },
                "plan": self.plan,
                "origin": self.origin,
                "reason": self.reason,
                "cached": self.cached,
                "kb_version": self.kb_store.current_version,
                "inventory_stale": False,
                "created_at": self.record["created_at"] if self.record else None,
                "devin_session_id": (
                    self.record["devin_session_id"] if self.record else self.session_id
                ),
                "label": (
                    "Local draft (Devin unavailable)" if self.origin == "local_draft" else None
                ),
            }

    def _phase(self, state: str, phase: str | None = None) -> None:
        with self.lock:
            self.state = state
            self.phase = phase or state

    def _terminate_session(self) -> None:
        with self._termination_lock:
            if self._termination_attempted or self.client is None or not self.session_id:
                return
            self._termination_attempted = True
            client = self.client
            session_id = self.session_id
        try:
            client.terminate_session(session_id)
        except Exception as error:
            try:
                self.audit.append(
                    "system",
                    "plan.terminate_failed",
                    session_id,
                    {"error_type": type(error).__name__},
                )
            except Exception:
                pass

    def cancel(self) -> None:
        self.cancel_event.set()
        self._terminate_session()
        self._phase("cancelled")
        self.audit.append(
            "user",
            "plan.cancel",
            f"{self.source}:{self.target}",
            {"kb_version": self.kb_store.current_version},
        )

    def _fallback(self, context: PlanContext | None, reason: str) -> None:
        if self.cancel_event.is_set():
            self._terminate_session()
            self._phase("cancelled")
            return
        plan = build_local_draft(
            self.source,
            self.target,
            self.db_path,
            inventory_aggregates=self.inventory_aggregates,
        )
        record = self.plan_store.save(
            plan,
            source=self.source,
            target=self.target,
            kb_version=int(self.kb_store.current_version or 1),
            origin="local_draft",
            devin_session_id=self.session_id,
            devin_url=self.devin_url,
            reason=reason,
            inventory_fingerprint=self.inventory_fingerprint,
        )
        with self.lock:
            self.state = "failed_fallback"
            self.phase = "failed_fallback"
            self.origin = "local_draft"
            self.reason = reason
            self.plan = record["payload"]
            self.record = record
        self.audit.append(
            "system",
            "plan.fallback",
            f"{self.source}:{self.target}",
            {"reason": reason, "kb_version": record["kb_version"]},
        )

    def _validated_plan(self, value: dict, context: PlanContext) -> Plan:
        plan = Plan.model_validate(value)
        sections = []
        for section in plan.sections:
            citations = []
            unverified = section.unverified_citations
            for citation in section.citations:
                if citation.kb_id is not None:
                    if citation.kb_id not in context.kb_ids:
                        unverified = True
                        continue
                else:
                    parts = urlsplit(citation.url or "")
                    if parts.scheme.casefold() != "https" or not parts.hostname:
                        raise ValueError("Citation URL must use HTTPS.")
                    if parts.hostname.casefold() not in context.allowed_hosts:
                        citation = citation.model_copy(update={"unverified": True})
                        unverified = True
                citations.append(citation)
            sections.append(
                section.model_copy(
                    update={"citations": citations, "unverified_citations": unverified}
                )
            )
        return plan.model_copy(update={"sections": sections})

    def _complete(self, value: dict, context: PlanContext) -> None:
        self._phase("validating")
        plan = self._validated_plan(value, context)
        record = self.plan_store.save(
            plan,
            source=self.source,
            target=self.target,
            kb_version=int(self.kb_store.current_version or 1),
            origin="devin",
            devin_session_id=self.session_id,
            devin_url=self.devin_url,
            inventory_fingerprint=self.inventory_fingerprint,
        )
        with self.lock:
            self.state = "done"
            self.phase = "done"
            self.origin = "devin"
            self.plan = record["payload"]
            self.record = record
        self.audit.append(
            "system",
            "plan.done",
            f"{self.source}:{self.target}",
            {"kb_version": record["kb_version"]},
        )

    def run(self) -> None:
        context = None
        try:
            if self.client_factory and self.pat.startswith("apk_"):
                self.client = self.client_factory(self.base_url, self.org_id, self.pat)
            elif self.client_factory and self.pat.startswith("cog_") and self.org_id:
                self.client = self.client_factory(self.base_url, self.org_id, self.pat)
            elif os.environ.get("MIGRAAI_TEST_MOCK_DEVIN") == "1":
                self.client = build_devin_client(self.base_url, self.org_id, self.pat)
            elif self.pat.startswith("apk_"):
                self.client = DevinV1Client(self.base_url, self.pat)
            elif self.pat.startswith("cog_") and self.org_id:
                self.client = DevinClient(self.base_url, self.org_id, self.pat)
            else:
                self._fallback(context, "Devin token not configured")
                return
            self._phase("building_context")
            context = build_context(self.source, self.target, self.db_path)
            if self.cancel_event.is_set():
                self._phase("cancelled")
                return
            self._phase("uploading")
            attachment_url = self.client.upload_attachment(
                "migration-context.md", context.markdown.encode("utf-8")
            )
            prompt = build_prompt(
                self.source,
                self.target,
                attachment_url,
                inventory_aggregates=self.inventory_aggregates,
            )
            self._phase("creating_session")
            self.session_id, self.devin_url = self.client.create_session(
                prompt,
                schema=SCHEMA,
                max_acu_limit=self.acu_cap,
                title=f"{self.source} to {self.target} migration plan",
                tags=["migraai", "migration-plan"],
            )
            if self.cancel_event.is_set():
                self._terminate_session()
                self._phase("cancelled")
                return
            self._phase("running")
            deadline = time.monotonic() + self.timeout_minutes * 60
            interval = 15
            waiting_count = 0
            while not self.cancel_event.is_set():
                if time.monotonic() >= deadline:
                    self._fallback(context, "Devin session timed out")
                    return
                if self.cancel_event.wait(min(interval, max(0, deadline - time.monotonic()))):
                    self._terminate_session()
                    self._phase("cancelled")
                    return
                session = self.client.get_session(self.session_id)
                if self.cancel_event.is_set():
                    self._terminate_session()
                    self._phase("cancelled")
                    return
                state = str(session.get("state", "working")).casefold()
                detail = session.get("detail", "")
                output = session.get("structured_output")
                if state == "blocked" and output is None:
                    waiting_count += 1
                    if waiting_count >= 2:
                        self._fallback(context, "Devin asked for input")
                        return
                else:
                    waiting_count = 0
                if state == "done" and output is None:
                    self._fallback(context, "Devin returned an invalid plan")
                    return
                if state in {"done", "blocked"} and output is not None:
                    try:
                        self._complete(output, context)
                    except Exception:
                        self._fallback(context, "Devin returned an invalid plan")
                    return
                if state == "failed":
                    self._fallback(context, f"Devin session ended: {detail}")
                    return
                interval = min(interval * 2, 60)
        except Exception:
            if not self.cancel_event.is_set():
                self._fallback(context, "Devin request failed")
        finally:
            self._terminate_session()
            if self.client is not None:
                self.client.close()
