"""HTTP routes for OIDC login, callback, logout, and portal session bootstrap."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.api_dependencies import get_db_session, get_runtime_state
from ato_service.auth_context import AuthenticationRequiredError, require_authenticated_principal
from ato_service.app_runtime import AppRuntimeState
from ato_service.oidc_auth import (
    OidcAuthenticationError,
    build_authorization_redirect_url,
    exchange_code_for_identity,
)
from ato_service.session_auth import (
    ResolvedSessionSettings,
    SessionConfigurationError,
    SessionExpiredError,
    create_auth_session,
    create_oidc_login_state,
    consume_oidc_login_state,
    delete_auth_session,
    principal_from_session,
    resolve_session_settings,
    session_cookie_attributes,
    session_cookie_name,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _session_settings_from_runtime(
    runtime_state: AppRuntimeState,
) -> ResolvedSessionSettings:
    settings = resolve_session_settings(runtime_state.config)
    if settings is None:
        raise SessionConfigurationError()
    return settings


def create_auth_router() -> APIRouter:
    """Build OIDC/session routes used by the portal."""
    router = APIRouter()

    @router.get("/auth/login", tags=["Auth"])
    async def get_auth_login(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[AppRuntimeState, Depends(get_runtime_state)],
    ) -> RedirectResponse:
        settings = _session_settings_from_runtime(runtime_state)
        login_state = await create_oidc_login_state(session, now=_utc_now())
        redirect_url = build_authorization_redirect_url(
            settings=settings,
            state_token=login_state.state_token,
            code_verifier=login_state.code_verifier,
            nonce=login_state.nonce,
        )
        return RedirectResponse(url=redirect_url, status_code=302)

    @router.get("/auth/callback", tags=["Auth"])
    async def get_auth_callback(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[AppRuntimeState, Depends(get_runtime_state)],
        code: str | None = None,
        state: str | None = None,
    ) -> RedirectResponse:
        settings = _session_settings_from_runtime(runtime_state)
        if not code or not state:
            raise OidcAuthenticationError()
        login_state = await consume_oidc_login_state(
            session,
            state_token=state,
            now=_utc_now(),
        )
        identity = await exchange_code_for_identity(
            config=runtime_state.config,
            settings=settings,
            code=code,
            code_verifier=login_state.code_verifier,
            nonce=login_state.nonce,
        )
        session_row = await create_auth_session(
            session,
            actor_id=identity.actor_id,
            groups=list(identity.groups),
            portal_origin=settings.portal_public_origin,
            settings=settings,
            now=_utc_now(),
        )
        cookie_name = session_cookie_name(secure_cookie=settings.secure_cookie)
        redirect_response = RedirectResponse(
            url=settings.portal_public_origin.rstrip("/") + "/",
            status_code=302,
        )
        redirect_response.set_cookie(
            cookie_name,
            str(session_row.session_id),
            **session_cookie_attributes(settings=settings),
        )
        return redirect_response

    @router.get("/auth/session", tags=["Auth"])
    async def get_auth_session(request: Request) -> dict[str, Any]:
        principal = require_authenticated_principal(request)
        return {
            "actor_id": principal.actor_id,
            "groups": list(principal.groups),
            "csrf_token": principal.csrf_token,
            "portal_origin": principal.allowed_origins[0],
        }

    @router.post("/auth/logout", status_code=204, tags=["Auth"])
    async def post_auth_logout(
        request: Request,
        response: Response,
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[AppRuntimeState, Depends(get_runtime_state)],
    ) -> Response:
        settings = _session_settings_from_runtime(runtime_state)
        cookie_name = session_cookie_name(secure_cookie=settings.secure_cookie)
        raw_session_id = request.cookies.get(cookie_name)
        if raw_session_id:
            try:
                session_id = uuid.UUID(raw_session_id)
            except ValueError:
                session_id = None
            if session_id is not None:
                await delete_auth_session(session, session_id=session_id)
        response = Response(status_code=204)
        response.delete_cookie(cookie_name, path="/")
        return response

    return router
