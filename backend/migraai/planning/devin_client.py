"""Devin v3 API references:

Attachments: https://docs.devin.ai/api-reference/v3/attachments/post-organizations-attachments
Create session: https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions
Get session: https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session
Terminate session: https://docs.devin.ai/api-reference/v3/sessions/delete-organizations-sessions
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

import httpx


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
        return response.json()

    def terminate_session(self, session_id: str) -> None:
        response = self.client.delete(self._session_path(session_id))
        response.raise_for_status()

    def _session_path(self, session_id: str) -> str:
        if not session_id.startswith("devin-"):
            raise ValueError("Devin session ID must start with 'devin-'.")
        return f"/v3/organizations/{self.org_id}/sessions/{session_id}"

    def close(self) -> None:
        self.client.close()
