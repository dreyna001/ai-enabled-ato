"""ASGI middleware that injects authenticated principals from portal session cookies."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ato_service.app_runtime import RUNTIME_STATE_ATTR, AppRuntimeState
from ato_service.auth_security_audit import record_session_revoked
from ato_service.session_auth import (
    SessionExpiredError,
    load_valid_session,
    principal_from_session,
    resolve_session_settings,
    session_cookie_name,
)


class SessionAuthenticationMiddleware(BaseHTTPMiddleware):
    """Load OIDC-backed sessions and attach principals to API requests."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        runtime_state = getattr(request.app.state, RUNTIME_STATE_ATTR, None)
        if not isinstance(runtime_state, AppRuntimeState):
            return await call_next(request)
        if runtime_state.session_factory is None:
            return await call_next(request)

        settings = resolve_session_settings(runtime_state.config)
        if settings is None:
            return await call_next(request)

        cookie_name = session_cookie_name(secure_cookie=settings.secure_cookie)
        raw_session_id = request.cookies.get(cookie_name)
        if not raw_session_id:
            return await call_next(request)

        try:
            session_id = uuid.UUID(raw_session_id)
        except ValueError:
            return await call_next(request)

        async with runtime_state.session_factory() as db_session:
            try:
                session_row = await load_valid_session(
                    db_session,
                    session_id=session_id,
                    settings=settings,
                    now=datetime.now(timezone.utc),
                )
                await db_session.commit()
            except SessionExpiredError as exc:
                await db_session.rollback()
                if (
                    exc.revocation_reason in {"idle_timeout", "absolute_timeout"}
                    and runtime_state.audit_hmac_key is not None
                    and exc.actor_id is not None
                    and exc.session_id is not None
                ):
                    async with runtime_state.session_factory() as audit_session:
                        try:
                            await record_session_revoked(
                                audit_session,
                                hmac_key=runtime_state.audit_hmac_key,
                                actor_id=exc.actor_id,
                                session_id=exc.session_id,
                                reason=exc.revocation_reason,
                                now=datetime.now(timezone.utc),
                            )
                            await audit_session.commit()
                        except Exception:
                            await audit_session.rollback()
                return await call_next(request)
            except Exception:
                await db_session.rollback()
                raise
            else:
                request.state.authenticated_principal = principal_from_session(
                    session_row
                )

        return await call_next(request)
