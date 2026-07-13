"""Production OIDC id_token validation with Authlib and bounded JWKS caching."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from authlib.jose import JsonWebKey, jwt
from authlib.jose.errors import JoseError

from ato_service.oidc_auth import OidcAuthenticationError
from ato_service.session_auth import ResolvedSessionSettings

OIDC_HTTP_TIMEOUT_SECONDS = 10.0
OIDC_JWKS_CACHE_TTL_SECONDS = 300
OIDC_JWT_CLOCK_SKEW_SECONDS = 60
ALLOWED_JWT_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"})


@dataclass(frozen=True, slots=True)
class _JwksCacheEntry:
    fetched_at: float
    key_set: JsonWebKey


_JWKS_CACHE: dict[str, _JwksCacheEntry] = {}


def _issuer_base(issuer_url: str) -> str:
    return issuer_url.rstrip("/") + "/"


async def _fetch_json(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=OIDC_HTTP_TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers={"Accept": "application/json"})
    if response.status_code != 200:
        raise OidcAuthenticationError()
    payload = response.json()
    if not isinstance(payload, dict):
        raise OidcAuthenticationError()
    return payload


async def _discover_jwks_uri(issuer_url: str) -> str:
    discovery_url = urljoin(_issuer_base(issuer_url), ".well-known/openid-configuration")
    document = await _fetch_json(discovery_url)
    jwks_uri = document.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri.strip():
        raise OidcAuthenticationError()
    return jwks_uri.strip()


async def _load_jwks(issuer_url: str, *, force_refresh: bool = False) -> JsonWebKey:
    cache_key = issuer_url.rstrip("/")
    now = time.monotonic()
    cached = _JWKS_CACHE.get(cache_key)
    if (
        not force_refresh
        and cached is not None
        and now - cached.fetched_at < OIDC_JWKS_CACHE_TTL_SECONDS
    ):
        return cached.key_set

    jwks_uri = await _discover_jwks_uri(issuer_url)
    jwks_document = await _fetch_json(jwks_uri)
    keys = jwks_document.get("keys")
    if not isinstance(keys, list) or not keys:
        raise OidcAuthenticationError()
    key_set = JsonWebKey.import_key_set({"keys": keys})
    _JWKS_CACHE[cache_key] = _JwksCacheEntry(fetched_at=now, key_set=key_set)
    return key_set


def _decode_jwt_segment(segment: str) -> dict[str, Any]:
    padding = "=" * (-len(segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(segment + padding)
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        raise OidcAuthenticationError() from None
    if not isinstance(payload, dict):
        raise OidcAuthenticationError()
    return payload


def _header_alg_and_kid(id_token: str) -> tuple[str, str | None]:
    parts = id_token.split(".")
    if len(parts) != 3:
        raise OidcAuthenticationError()
    header = _decode_jwt_segment(parts[0])
    algorithm = header.get("alg")
    if not isinstance(algorithm, str) or not algorithm:
        raise OidcAuthenticationError()
    kid = header.get("kid")
    if kid is not None and not isinstance(kid, str):
        raise OidcAuthenticationError()
    if header.get("jwk") is not None:
        raise OidcAuthenticationError()
    return algorithm, kid


def _resolve_verification_key(
    key_set: JsonWebKey,
    *,
    algorithm: str,
    kid: str | None,
) -> Any:
    if algorithm not in ALLOWED_JWT_ALGORITHMS:
        raise OidcAuthenticationError()
    try:
        if kid:
            key = key_set.find_by_kid(kid)
        elif len(getattr(key_set, "keys", [])) == 1:
            key = key_set.keys[0]
        else:
            key = None
    except ValueError:
        key = None
    if key is None:
        raise OidcAuthenticationError()
    return key


async def _resolve_key_with_rotation(
    *,
    issuer_url: str,
    algorithm: str,
    kid: str | None,
) -> Any:
    key_set = await _load_jwks(issuer_url)
    try:
        return _resolve_verification_key(key_set, algorithm=algorithm, kid=kid)
    except OidcAuthenticationError:
        if not kid:
            raise
    refreshed = await _load_jwks(issuer_url, force_refresh=True)
    return _resolve_verification_key(refreshed, algorithm=algorithm, kid=kid)


def _validate_claims(
    claims: dict[str, Any],
    *,
    settings: ResolvedSessionSettings,
    nonce: str,
) -> None:
    issuer = claims.get("iss")
    if issuer != settings.oidc_issuer_url.rstrip("/"):
        raise OidcAuthenticationError()
    audience = claims.get("aud")
    if isinstance(audience, list):
        if settings.oidc_audience not in audience:
            raise OidcAuthenticationError()
    elif audience != settings.oidc_audience:
        raise OidcAuthenticationError()
    token_nonce = claims.get("nonce")
    if token_nonce != nonce:
        raise OidcAuthenticationError()
    exp = claims.get("exp")
    iat = claims.get("iat")
    if not isinstance(exp, (int, float)) or not isinstance(iat, (int, float)):
        raise OidcAuthenticationError()
    now = time.time()
    if exp < now - OIDC_JWT_CLOCK_SKEW_SECONDS:
        raise OidcAuthenticationError()
    if iat > now + OIDC_JWT_CLOCK_SKEW_SECONDS:
        raise OidcAuthenticationError()


async def validate_production_id_token(
    *,
    id_token: str,
    settings: ResolvedSessionSettings,
    nonce: str,
) -> dict[str, Any]:
    """Validate a production id_token signature and required claims."""
    algorithm, kid = _header_alg_and_kid(id_token)
    key = await _resolve_key_with_rotation(
        issuer_url=settings.oidc_issuer_url,
        algorithm=algorithm,
        kid=kid,
    )
    claims_options = {
        "iss": {"essential": True, "value": settings.oidc_issuer_url.rstrip("/")},
        "aud": {"essential": True, "value": settings.oidc_audience},
        "exp": {"essential": True},
        "iat": {"essential": True},
        "nonce": {"essential": True, "value": nonce},
    }
    try:
        claims = jwt.decode(
            id_token,
            key,
            claims_options=claims_options,
        )
        claims.validate(leeway=OIDC_JWT_CLOCK_SKEW_SECONDS)
    except JoseError:
        raise OidcAuthenticationError() from None
    if not isinstance(claims, dict):
        raise OidcAuthenticationError()
    _validate_claims(claims, settings=settings, nonce=nonce)
    return claims


def clear_jwks_cache_for_tests() -> None:
    """Reset the in-process JWKS cache (tests only)."""
    _JWKS_CACHE.clear()


__all__ = [
    "ALLOWED_JWT_ALGORITHMS",
    "OIDC_HTTP_TIMEOUT_SECONDS",
    "OIDC_JWKS_CACHE_TTL_SECONDS",
    "OIDC_JWT_CLOCK_SKEW_SECONDS",
    "clear_jwks_cache_for_tests",
    "validate_production_id_token",
]
