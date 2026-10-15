from __future__ import annotations

import hmac
import os
import secrets
import threading
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse, RedirectResponse


class AuthState:
    def __init__(self, launch_token: str | None = None) -> None:
        self.launch_token = (
            launch_token or os.environ.get("MIGRAAI_DEV_TOKEN") or secrets.token_urlsafe(32)
        )
        self._used = False
        self._sessions: set[str] = set()
        self._lock = threading.Lock()

    def exchange(self, token: str) -> tuple[str, str] | None:
        with self._lock:
            if self._used or not hmac.compare_digest(token, self.launch_token):
                return None
            self._used = True
            session_id = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            self._sessions.add(session_id)
            return session_id, csrf_token

    def valid_session(self, session_id: str | None) -> bool:
        return bool(session_id and session_id in self._sessions)


def launch_response(token: str, auth_state: AuthState) -> Response:
    exchanged = auth_state.exchange(token)
    if exchanged is None:
        return JSONResponse({"detail": "Invalid or already used launch token."}, status_code=403)
    session_id, csrf_token = exchanged
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "migraai_session",
        session_id,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )
    response.set_cookie(
        "migraai_csrf",
        csrf_token,
        httponly=False,
        samesite="strict",
        secure=False,
        path="/",
    )
    return response


async def security_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    auth_state: AuthState,
) -> Response:
    if request.url.path.startswith("/api/"):
        host = request.headers.get("host", "").casefold()
        server = request.scope.get("server")
        port = server[1] if server else None
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if host not in allowed:
            return JSONResponse({"detail": "Invalid Host header."}, status_code=400)

        path = request.url.path
        if path not in {"/api/health", "/api/auth/launch"}:
            session_id = request.cookies.get("migraai_session")
            if not auth_state.valid_session(session_id):
                return JSONResponse({"detail": "Authentication required."}, status_code=401)
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                csrf_cookie = request.cookies.get("migraai_csrf", "")
                csrf_header = request.headers.get("x-migraai-csrf", "")
                if not csrf_cookie or not hmac.compare_digest(csrf_cookie, csrf_header):
                    return JSONResponse({"detail": "CSRF validation failed."}, status_code=403)
    return await call_next(request)
