from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from migraai.core.security import AuthState, launch_response

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/launch")
def exchange_launch_token(request: Request, token: str) -> Response:
    auth_state: AuthState = request.app.state.auth
    return launch_response(token, auth_state)
