"""OIDC Authorization Code + PKCE client and dev-local loopback issuer."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ato_service.credentials import (
    CredentialResolutionError,
    resolve_secret_bytes_from_credential_reference,
)
from ato_service.runtime_config import RuntimeConfig
from ato_service.session_auth import ResolvedSessionSettings

DEV_OIDC_PATH_PREFIX = "/dev-oidc"
DEV_OIDC_ACTOR_ID = "dev-portal-user"
DEV_OIDC_GROUPS = ("owners", "viewers")


class OidcAuthenticationError(Exception):
    """Raised when OIDC token exchange or claim validation fails."""

    error_code = "authentication_required"


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    """Validated identity claims from an OIDC token response."""

    actor_id: str
    groups: tuple[str, ...]


def _pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise OidcAuthenticationError()
    payload_segment = parts[1]
    padding = "=" * (-len(payload_segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_segment + padding)
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        raise OidcAuthenticationError() from None
    if not isinstance(payload, dict):
        raise OidcAuthenticationError()
    return payload


def _groups_from_claims(claims: dict[str, Any]) -> tuple[str, ...]:
    raw_groups = claims.get("groups")
    if isinstance(raw_groups, list):
        normalized = [value for value in raw_groups if isinstance(value, str) and value.strip()]
        if normalized:
            return tuple(normalized)
    return DEV_OIDC_GROUPS


def is_embedded_dev_oidc_issuer(
    config: RuntimeConfig,
    settings: ResolvedSessionSettings,
) -> bool:
    """Return True when the configured issuer is the built-in dev loopback IdP."""
    if config.runtime_profile != "dev_local":
        return False
    issuer = settings.oidc_issuer_url.rstrip("/")
    return issuer.endswith(DEV_OIDC_PATH_PREFIX)


def resolve_oidc_client_secret(config: RuntimeConfig) -> bytes:
    """Resolve the OIDC client secret bytes from runtime credential references."""
    reference = config.document.get("OIDC_CLIENT_CREDENTIAL_REFERENCE")
    if not isinstance(reference, dict):
        raise CredentialResolutionError("OIDC client credential reference is required")
    enforce_root = config.runtime_profile == "onprem_production"
    return resolve_secret_bytes_from_credential_reference(
        reference,
        enforce_root_owned_file_metadata=enforce_root,
    )


def build_oidc_redirect_uri(settings: ResolvedSessionSettings) -> str:
    """Return the portal callback URI registered with the OIDC provider."""
    origin = settings.portal_public_origin.rstrip("/")
    return f"{origin}/api/v1/auth/callback"


def build_authorization_redirect_url(
    *,
    settings: ResolvedSessionSettings,
    state_token: str,
    code_verifier: str,
    nonce: str,
) -> str:
    """Build the OIDC authorization redirect for the browser login kickoff."""
    redirect_uri = build_oidc_redirect_uri(settings)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_audience,
            "redirect_uri": redirect_uri,
            "scope": "openid profile groups",
            "state": state_token,
            "nonce": nonce,
            "code_challenge": _pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
    )
    authorize_url = urljoin(settings.oidc_issuer_url.rstrip("/") + "/", "authorize")
    return f"{authorize_url}?{query}"


async def exchange_code_for_identity(
    *,
    config: RuntimeConfig,
    settings: ResolvedSessionSettings,
    code: str,
    code_verifier: str,
    nonce: str,
) -> OidcIdentity:
    """Exchange an authorization code and validate returned identity claims."""
    if is_embedded_dev_oidc_issuer(config, settings):
        return OidcIdentity(actor_id=DEV_OIDC_ACTOR_ID, groups=DEV_OIDC_GROUPS)

    redirect_uri = build_oidc_redirect_uri(settings)
    token_url = urljoin(settings.oidc_issuer_url.rstrip("/") + "/", "token")
    client_secret = resolve_oidc_client_secret(config).decode("utf-8")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": settings.oidc_audience,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code != 200:
        raise OidcAuthenticationError()
    payload = response.json()
    if not isinstance(payload, dict):
        raise OidcAuthenticationError()
    id_token = payload.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise OidcAuthenticationError()

    claims = _decode_jwt_payload(id_token)
    token_nonce = claims.get("nonce")
    if token_nonce != nonce:
        raise OidcAuthenticationError()
    audience = claims.get("aud")
    if isinstance(audience, list):
        if settings.oidc_audience not in audience:
            raise OidcAuthenticationError()
    elif audience != settings.oidc_audience:
        raise OidcAuthenticationError()

    actor_id = claims.get("sub")
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise OidcAuthenticationError()
    return OidcIdentity(actor_id=actor_id.strip(), groups=_groups_from_claims(claims))


def create_dev_oidc_router() -> APIRouter:
    """Expose a minimal loopback OIDC issuer for dev_local portal authentication."""
    router = APIRouter()

    @router.get("/.well-known/openid-configuration")
    async def openid_configuration(request: Request) -> dict[str, Any]:
        issuer = str(request.base_url).rstrip("/")
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
            "id_token_signing_alg_values_supported": ["none"],
        }

    @router.get("/authorize")
    async def authorize(request: Request) -> RedirectResponse:
        query = dict(request.query_params)
        redirect_uri = query.get("redirect_uri")
        state = query.get("state")
        if not redirect_uri or not state:
            return RedirectResponse(url="/", status_code=302)
        code = secrets.token_urlsafe(24)
        location = f"{redirect_uri}?{urlencode({'code': code, 'state': state})}"
        return RedirectResponse(url=location, status_code=302)

    @router.post("/token")
    async def token() -> JSONResponse:
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8")
        ).rstrip(b"=").decode("ascii")
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": DEV_OIDC_ACTOR_ID,
                    "aud": "ato-portal-dev",
                    "groups": list(DEV_OIDC_GROUPS),
                    "nonce": "dev",
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 300,
                }
            ).encode("utf-8")
        ).rstrip(b"=").decode("ascii")
        id_token = f"{header}.{payload}."
        return JSONResponse({"id_token": id_token, "token_type": "Bearer", "expires_in": 300})

    return router
