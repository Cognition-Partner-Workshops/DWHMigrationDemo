from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

logger = logging.getLogger(__name__)


class SessionClient(Protocol):
    def upload_attachment(self, filename: str, content: bytes) -> str: ...

    def create_session(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        max_acu_limit: int | None,
        title: str,
        tags: list[str],
    ) -> tuple[str, str]: ...

    def get_session(self, session_id: str) -> dict[str, Any]: ...

    def terminate_session(self, session_id: str) -> None: ...


@dataclass(slots=True)
class SessionOutcome:
    session_id: str | None
    url: str | None
    state: Literal["done", "failed", "timeout", "cancelled", "blocked"]
    structured_output: Any | None
    acus: float | None
    error: str | None


def _error_description(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"Devin returned HTTP {error.response.status_code}."
    if isinstance(error, httpx.TransportError):
        return f"Devin transport error: {type(error).__name__}."
    return f"Devin request failed: {type(error).__name__}."


def _create_with_retry(
    client: SessionClient,
    prompt: str,
    *,
    schema: dict[str, Any],
    title: str,
    tags: list[str],
) -> tuple[str, str]:
    for attempt in range(2):
        try:
            return client.create_session(
                prompt,
                schema=schema,
                max_acu_limit=None,
                title=title,
                tags=tags,
            )
        except httpx.TransportError:
            if attempt:
                raise
        except httpx.HTTPStatusError as error:
            if attempt or error.response.status_code < 500:
                raise
    raise RuntimeError("Session creation did not return a result.")


def run_session(
    client: SessionClient,
    *,
    prompt: str,
    attachment_name: str,
    attachment_md: str,
    schema: dict[str, Any],
    title: str,
    tags: list[str],
    timeout_s: float,
    cancel: threading.Event,
    poll_initial: float = 5,
    poll_max: float = 30,
) -> SessionOutcome:
    session_id: str | None = None
    url: str | None = None
    try:
        attachment_url = client.upload_attachment(attachment_name, attachment_md.encode("utf-8"))
        if cancel.is_set():
            return SessionOutcome(None, None, "cancelled", None, None, None)
        session_prompt = f'{prompt.rstrip()}\nATTACHMENT:"{attachment_url}"'
        session_id, url = _create_with_retry(
            client,
            session_prompt,
            schema=schema,
            title=title,
            tags=tags,
        )
    except Exception as error:
        return SessionOutcome(None, None, "failed", None, None, _error_description(error))

    deadline = time.monotonic() + max(0, timeout_s)
    interval = max(0, poll_initial)
    last_acus: float | None = None
    try:
        while True:
            if cancel.is_set():
                return SessionOutcome(
                    session_id, url, "cancelled", None, last_acus, "Session cancelled."
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return SessionOutcome(
                    session_id, url, "timeout", None, last_acus, "Session timed out."
                )
            if cancel.wait(min(interval, remaining)):
                return SessionOutcome(
                    session_id, url, "cancelled", None, last_acus, "Session cancelled."
                )
            if time.monotonic() >= deadline:
                return SessionOutcome(
                    session_id, url, "timeout", None, last_acus, "Session timed out."
                )
            try:
                session = client.get_session(session_id)
            except Exception as error:
                return SessionOutcome(
                    session_id, url, "failed", None, last_acus, _error_description(error)
                )

            acus = session.get("acus")
            if isinstance(acus, (int, float)) and not isinstance(acus, bool):
                last_acus = float(acus)
            state = session.get("state")
            output = session.get("structured_output")
            detail = session.get("detail")
            if state == "done":
                if output is None:
                    return SessionOutcome(
                        session_id,
                        url,
                        "failed",
                        None,
                        last_acus,
                        "Session completed without output.",
                    )
                return SessionOutcome(session_id, url, "done", output, last_acus, None)
            if state == "failed":
                return SessionOutcome(
                    session_id,
                    url,
                    "failed",
                    output,
                    last_acus,
                    str(detail or "Session failed."),
                )
            if state == "blocked":
                if output is not None:
                    return SessionOutcome(session_id, url, "done", output, last_acus, None)
                return SessionOutcome(
                    session_id,
                    url,
                    "blocked",
                    None,
                    last_acus,
                    str(detail or "Session is blocked."),
                )
            interval = min(max(interval * 2, 0.1), max(0.1, poll_max))
    finally:
        if session_id is not None:
            try:
                client.terminate_session(session_id)
            except Exception as error:
                logger.warning("Could not terminate Devin session (%s).", type(error).__name__)
