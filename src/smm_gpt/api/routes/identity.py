"""Same-origin browser identity and tenant API. Disabled authentication never grants access."""

from typing import Annotated, cast
from uuid import UUID

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from smm_gpt.core.request_context import request_id
from smm_gpt.domain.access import AccessDenied, Principal
from smm_gpt.services.sessions import SessionService


class APIError(BaseModel):
    detail: str


router = APIRouter(responses={status: {"model": APIError} for status in (401, 403, 409, 422, 503)})
SESSION_COOKIE = "__Host-smm-session"
LOGIN_COOKIE = "__Host-smm-login"
CSRF_COOKIE = "__Host-smm-csrf"


def service(request: Request) -> SessionService:
    value = cast(SessionService | None, request.app.state.sessions)
    if value is None:
        raise HTTPException(503, "authentication_not_configured")
    return value


def correlation(request: Request) -> UUID:
    # Do not reflect arbitrary client-supplied IDs into audit or logs.
    if not hasattr(request.state, "request_id"):
        request.state.request_id = request_id()
    return cast(UUID, request.state.request_id)


async def principal(request: Request) -> Principal:
    sessions = service(request)
    changing = request.method not in {"GET", "HEAD", "OPTIONS"}
    try:
        if changing and (
            request.headers.get("origin") != sessions.settings.web_origin
            or not request.headers.get("x-csrf-token")
        ):
            raise AccessDenied("csrf_denied")
        token = request.cookies.get(SESSION_COOKIE, "")
        if not token or len(token) > 128:
            raise AccessDenied("invalid_session")
        return await sessions.authenticate(
            token, request.headers.get("x-csrf-token") if changing else None
        )
    except AccessDenied:
        await sessions.access.record_denial(None, correlation(request), "session.authenticate")
        raise HTTPException(403 if changing else 401, "authentication_required") from None


@router.get("/auth/login", response_model=None, status_code=303)
async def login(request: Request) -> RedirectResponse:
    sessions = service(request)
    try:
        url, browser = await sessions.begin_login()
    except (AccessDenied, httpx.HTTPError, KeyError, ValueError, TypeError):
        raise HTTPException(503, "identity_provider_unavailable") from None
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        LOGIN_COOKIE, browser, max_age=300, secure=True, httponly=True, samesite="lax"
    )
    return response


@router.get("/auth/callback", response_model=None, status_code=303)
async def callback(request: Request) -> RedirectResponse:
    sessions = service(request)
    query = request.query_params
    try:
        if (
            query.get("iss", sessions.settings.oidc_issuer_url) != sessions.settings.oidc_issuer_url
            or not query.get("code")
            or not query.get("state")
            or len(query["code"]) > 4096
            or len(query["state"]) > 128
        ):
            raise AccessDenied("invalid_callback")
        token, csrf = await sessions.finish_login(
            query["state"],
            request.cookies.get(LOGIN_COOKIE, ""),
            query["code"],
            correlation(request),
            request.cookies.get(SESSION_COOKIE, ""),
        )
    except (AccessDenied, httpx.HTTPError, jwt.PyJWTError, KeyError, ValueError, TypeError):
        await sessions.access.record_denial(None, correlation(request), "session.login")
        raise HTTPException(401, "login_failed") from None
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="lax")
    response.set_cookie(SESSION_COOKIE, token, secure=True, httponly=True, samesite="lax")
    response.set_cookie(CSRF_COOKIE, csrf, secure=True, httponly=False, samesite="strict")
    return response


@router.get("/auth/session")
async def session_info(user: Annotated[Principal, Depends(principal)]) -> dict[str, str | bool]:
    return {"user_id": str(user.user_id), "mfa": user.mfa}


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, user: Annotated[Principal, Depends(principal)]) -> Response:
    await service(request).logout(user, correlation(request))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, secure=True, samesite="strict")
    return response


@router.get("/workspaces/{workspace_id}")
async def workspace(
    request: Request, workspace_id: UUID, user: Annotated[Principal, Depends(principal)]
) -> dict[str, str]:
    return await service(request).access.workspace(user, workspace_id, correlation(request))


class DiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=128)


@router.post("/workspaces/{workspace_id}/diagnostic-jobs", status_code=201)
async def diagnostic(
    request: Request,
    workspace_id: UUID,
    body: DiagnosticRequest,
    user: Annotated[Principal, Depends(principal)],
) -> dict[str, UUID]:
    job = await service(request).access.create_job(
        user, workspace_id, body.idempotency_key, correlation(request)
    )
    return {"job_id": job}
