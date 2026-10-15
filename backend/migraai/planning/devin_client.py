"""Devin API references:

V1 upload: https://docs.devin.ai/api-reference/v1/attachments/upload-files-for-devin-to-work-with
V1 create: https://docs.devin.ai/api-reference/v1/sessions/create-a-new-devin-session
V1 get: https://docs.devin.ai/api-reference/v1/sessions/retrieve-details-about-an-existing-session
V1 terminate: https://docs.devin.ai/api-reference/v1/sessions/terminate-a-session
V3 attachments: https://docs.devin.ai/api-reference/v3/attachments/post-organizations-attachments
V3 create: https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions
V3 get: https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session
V3 terminate: https://docs.devin.ai/api-reference/v3/sessions/delete-organizations-sessions
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

import httpx


def _status_detail(*values: object) -> str:
    details = list(
        dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip())
    )
    return "; ".join(details)


def normalize_base_url(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username:
        raise ValueError("Devin API base URL must include an HTTP(S) host.")
    host = parts.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, "", "", "")).rstrip("/")


class DevinClient:
    def __init__(
        self,
        base_url: str,
        org_id: str,
        pat: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not org_id.startswith("org-") or len(org_id) <= 4:
            raise ValueError("Devin organization ID must start with 'org-'.")
        self.base_url = normalize_base_url(base_url)
        self.org_id = org_id
        self.pat = pat
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {pat}"},
            transport=transport,
            timeout=30,
        )

    def upload_attachment(self, name: str, data: bytes) -> str:
        response = self.client.post(
            f"/v3/organizations/{self.org_id}/attachments",
            files={"file": (name, data, "text/markdown")},
        )
        response.raise_for_status()
        return str(response.json()["url"])

    def create_session(
        self,
        prompt: str,
        *,
        schema: dict,
        max_acu_limit: int,
        title: str,
        tags: list[str],
    ) -> tuple[str, str]:
        attachment_match = re.search(r'^ATTACHMENT:"(https?://[^"]+)"$', prompt, re.MULTILINE)
        attachment_urls = [attachment_match.group(1)] if attachment_match else []
        response = self.client.post(
            f"/v3/organizations/{self.org_id}/sessions",
            json={
                "prompt": prompt,
                "attachment_urls": attachment_urls,
                "structured_output_schema": schema,
                "structured_output_required": True,
                "max_acu_limit": max_acu_limit,
                "resumable": False,
                "title": title,
                "tags": tags,
            },
        )
        response.raise_for_status()
        result = response.json()
        session_id = str(result["session_id"])
        if not session_id.startswith("devin-"):
            raise ValueError("Devin returned a session ID without the required 'devin-' prefix.")
        return session_id, str(result["url"])

    def get_session(self, session_id: str) -> dict:
        response = self.client.get(self._session_path(session_id))
        response.raise_for_status()
        result = response.json()
        status = str(result.get("status") or "")
        detail = str(result.get("status_detail") or "")
        status_normalized = status.casefold()
        detail_normalized = detail.casefold()
        if status_normalized == "exit" or detail_normalized == "finished":
            state = "done"
        elif detail_normalized in {"waiting_for_user", "waiting_for_approval"}:
            state = "blocked"
        elif status_normalized in {"error", "suspended"}:
            state = "failed"
        else:
            state = "working"
        return {
            "state": state,
            "detail": _status_detail(status, detail),
            "structured_output": result.get("structured_output"),
        }

    def terminate_session(self, session_id: str) -> None:
        response = self.client.delete(self._session_path(session_id))
        response.raise_for_status()

    def _session_path(self, session_id: str) -> str:
        if not session_id.startswith("devin-"):
            raise ValueError("Devin session ID must start with 'devin-'.")
        return f"/v3/organizations/{self.org_id}/sessions/{session_id}"

    def close(self) -> None:
        self.client.close()


class DevinV1Client:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=30,
        )

    def upload_attachment(self, name: str, data: bytes) -> str:
        response = self.client.post(
            "/v1/attachments",
            files={"file": (name, data, "text/markdown")},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("url"), str):
            return payload["url"]
        raise ValueError("Devin v1 attachment response did not contain a URL.")

    def create_session(
        self,
        prompt: str,
        *,
        schema: dict,
        max_acu_limit: int,
        title: str,
        tags: list[str],
    ) -> tuple[str, str]:
        response = self.client.post(
            "/v1/sessions",
            json={
                "prompt": prompt,
                "structured_output_schema": schema,
                "max_acu_limit": max_acu_limit,
                "title": title,
                "tags": tags,
                "idempotent": False,
            },
        )
        response.raise_for_status()
        result = response.json()
        return str(result["session_id"]), str(result["url"])

    def get_session(self, session_id: str) -> dict:
        response = self.client.get(f"/v1/sessions/{session_id}")
        response.raise_for_status()
        result = response.json()
        status = str(result.get("status") or "")
        status_enum = result.get("status_enum")
        enum_normalized = status_enum.casefold() if isinstance(status_enum, str) else ""
        if enum_normalized == "finished":
            state = "done"
        elif enum_normalized == "blocked":
            state = "blocked"
        elif enum_normalized == "expired" or enum_normalized.startswith("suspend_"):
            state = "failed"
        else:
            state = "working"
        return {
            "state": state,
            "detail": _status_detail(status, status_enum),
            "structured_output": result.get("structured_output"),
        }

    def terminate_session(self, session_id: str) -> None:
        response = self.client.delete(f"/v1/sessions/{session_id}")
        response.raise_for_status()

    def close(self) -> None:
        self.client.close()
