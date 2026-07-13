"""TM-002 OIDC/JWT validation tests (Authlib production path)."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import pytest
from authlib.jose import JsonWebKey, jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ato_service.oidc_auth import (
    DEV_OIDC_PATH_PREFIX,
    OidcAuthenticationError,
    exchange_code_for_identity,
    is_embedded_dev_oidc_issuer,
)
from ato_service.oidc_jwt import (
    clear_jwks_cache_for_tests,
    validate_production_id_token,
)
from ato_service.runtime_config import RuntimeConfig
from ato_service.session_auth import ResolvedSessionSettings


def _run(awaitable):
    return asyncio.run(awaitable)


def _runtime_config(*, profile: str, issuer: str) -> RuntimeConfig:
    return RuntimeConfig(
        runtime_profile=profile,
        storage_data_path=__import__("pathlib").Path("/data/ato-storage"),
        document={
            "runtime_profile": profile,
            "STORAGE_DATA_PATH": "/data/ato-storage",
            "OIDC_CLIENT_CREDENTIAL_REFERENCE": {
                "credential_kind": "file",
                "credential_identifier": "oidc-client-secret",
                "file_path": "/etc/ato-analyzer/credentials/oidc-client-secret",
            },
        },
    )


def _settings(*, issuer: str, audience: str = "ato-analyzer") -> ResolvedSessionSettings:
    return ResolvedSessionSettings(
        portal_public_origin="https://portal.example",
        oidc_issuer_url=issuer,
        oidc_audience=audience,
        idle_timeout=__import__("datetime").timedelta(minutes=30),
        absolute_timeout=__import__("datetime").timedelta(hours=8),
        secure_cookie=True,
    )


def _rsa_jwk(*, kid: str = "test-key-1") -> tuple[dict[str, Any], Any]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()
    private_jwk = JsonWebKey.import_key(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256"},
    )
    public_jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_base64url(public_numbers.n),
        "e": _int_to_base64url(public_numbers.e),
    }
    return public_jwk, private_jwk


def _int_to_base64url(value: int) -> str:
    import base64

    length = (value.bit_length() + 7) // 8
    encoded = base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")
    return encoded


def _issue_token(
    private_jwk: Any,
    *,
    issuer: str,
    audience: str,
    nonce: str,
    kid: str = "test-key-1",
    subject: str = "user-123",
    expires_in: int = 300,
    include_jwk_header: bool = False,
    algorithm: str = "RS256",
) -> str:
    now = int(time.time())
    header: dict[str, Any] = {"alg": algorithm, "typ": "JWT", "kid": kid}
    if include_jwk_header:
        header["jwk"] = {"kty": "RSA", "n": "abc", "e": "AQAB"}
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "nonce": nonce,
        "iat": now,
        "exp": now + expires_in,
        "groups": ["owners"],
    }
    token = jwt.encode(header, payload, private_jwk)
    if isinstance(token, bytes):
        token = token.decode("ascii")
    if kid != "test-key-1":
        header_segment, payload_segment, signature_segment = token.split(".")
        import base64
        import json

        patched_header = {**json.loads(base64.urlsafe_b64decode(header_segment + "==")), "kid": kid}
        encoded_header = (
            base64.urlsafe_b64encode(json.dumps(patched_header, separators=(",", ":")).encode())
            .rstrip(b"=")
            .decode("ascii")
        )
        token = ".".join([encoded_header, payload_segment, signature_segment])
    return token


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        payload = self._responses.get(url)
        if payload is None:
            return _FakeResponse({}, status_code=404)
        return _FakeResponse(payload)


@pytest.fixture(autouse=True)
def _clear_jwks_cache() -> None:
    clear_jwks_cache_for_tests()


@pytest.fixture
def issuer_urls() -> tuple[str, str, str]:
    issuer = "https://idp.example.internal"
    discovery = f"{issuer}/.well-known/openid-configuration"
    jwks_uri = f"{issuer}/jwks"
    return issuer, discovery, jwks_uri


@pytest.fixture
def signed_token_bundle(issuer_urls: tuple[str, str, str]) -> dict[str, Any]:
    issuer, _, _ = issuer_urls
    public_jwk, private_jwk = _rsa_jwk()
    nonce = str(uuid.uuid4())
    token = _issue_token(
        private_jwk,
        issuer=issuer,
        audience="ato-analyzer",
        nonce=nonce,
    )
    return {
        "issuer": issuer,
        "public_jwk": public_jwk,
        "private_jwk": private_jwk,
        "nonce": nonce,
        "token": token,
    }


def _patch_oidc_http(monkeypatch: pytest.MonkeyPatch, *, issuer_urls, public_jwk) -> None:
    _, discovery, jwks_uri = issuer_urls

    async def fake_fetch_json(url: str) -> dict[str, Any]:
        if url.endswith("/.well-known/openid-configuration"):
            return {"jwks_uri": jwks_uri}
        if url == jwks_uri or url.endswith("/jwks"):
            return {"keys": [public_jwk]}
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("ato_service.oidc_jwt._fetch_json", fake_fetch_json)


def test_validate_production_id_token_accepts_valid_token(
    monkeypatch: pytest.MonkeyPatch,
    issuer_urls,
    signed_token_bundle,
) -> None:
    _patch_oidc_http(monkeypatch, issuer_urls=issuer_urls, public_jwk=signed_token_bundle["public_jwk"])
    settings = _settings(issuer=signed_token_bundle["issuer"])
    claims = _run(
        validate_production_id_token(
            id_token=signed_token_bundle["token"],
            settings=settings,
            nonce=signed_token_bundle["nonce"],
        )
    )
    assert claims["sub"] == "user-123"


def test_validate_production_id_token_rejects_forged_signature(
    monkeypatch: pytest.MonkeyPatch,
    issuer_urls,
    signed_token_bundle,
) -> None:
    _patch_oidc_http(monkeypatch, issuer_urls=issuer_urls, public_jwk=signed_token_bundle["public_jwk"])
    settings = _settings(issuer=signed_token_bundle["issuer"])
    token = signed_token_bundle["token"]
    parts = token.split(".")
    sig = parts[2]
    forged = ".".join([parts[0], parts[1], sig[:-5] + ("aaaaa" if not sig.endswith("aaaaa") else "bbbbb")])
    with pytest.raises(OidcAuthenticationError):
        _run(
            validate_production_id_token(
                id_token=forged,
                settings=settings,
                nonce=signed_token_bundle["nonce"],
            )
        )


def test_validate_production_id_token_rejects_wrong_issuer(
    monkeypatch: pytest.MonkeyPatch,
    issuer_urls,
    signed_token_bundle,
) -> None:
    _patch_oidc_http(monkeypatch, issuer_urls=issuer_urls, public_jwk=signed_token_bundle["public_jwk"])
    settings = _settings(issuer="https://other-idp.example.internal")
    with pytest.raises(OidcAuthenticationError):
        _run(
            validate_production_id_token(
                id_token=signed_token_bundle["token"],
                settings=settings,
                nonce=signed_token_bundle["nonce"],
            )
        )


def test_validate_production_id_token_rejects_wrong_audience(
    monkeypatch: pytest.MonkeyPatch,
    issuer_urls,
    signed_token_bundle,
) -> None:
    _patch_oidc_http(monkeypatch, issuer_urls=issuer_urls, public_jwk=signed_token_bundle["public_jwk"])
    settings = _settings(issuer=signed_token_bundle["issuer"], audience="wrong-audience")
    with pytest.raises(OidcAuthenticationError):
        _run(
            validate_production_id_token(
                id_token=signed_token_bundle["token"],
                settings=settings,
                nonce=signed_token_bundle["nonce"],
            )
        )


def test_validate_production_id_token_rejects_expired_token(
    monkeypatch: pytest.MonkeyPatch,
    issuer_urls,
    signed_token_bundle,
) -> None:
    _patch_oidc_http(monkeypatch, issuer_urls=issuer_urls, public_jwk=signed_token_bundle["public_jwk"])
    expired = _issue_token(
        signed_token_bundle["private_jwk"],
        issuer=signed_token_bundle["issuer"],
        audience="ato-analyzer",
        nonce=signed_token_bundle["nonce"],
        expires_in=-600,
    )
    settings = _settings(issuer=signed_token_bundle["issuer"])
    with pytest.raises(OidcAuthenticationError):
        _run(
            validate_production_id_token(
                id_token=expired,
                settings=settings,
                nonce=signed_token_bundle["nonce"],
            )
        )


def test_validate_production_id_token_rejects_wrong_nonce(
    monkeypatch: pytest.MonkeyPatch,
    issuer_urls,
    signed_token_bundle,
) -> None:
    _patch_oidc_http(monkeypatch, issuer_urls=issuer_urls, public_jwk=signed_token_bundle["public_jwk"])
    settings = _settings(issuer=signed_token_bundle["issuer"])
    with pytest.raises(OidcAuthenticationError):
        _run(
            validate_production_id_token(
                id_token=signed_token_bundle["token"],
                settings=settings,
                nonce="wrong-nonce",
            )
        )


def test_validate_production_id_token_refreshes_jwks_on_kid_miss(
    monkeypatch: pytest.MonkeyPatch,
    issuer_urls,
    signed_token_bundle,
) -> None:
    issuer, discovery, jwks_uri = issuer_urls
    rotated_public, rotated_private = _rsa_jwk(kid="rotated-key")
    fetch_calls: list[str] = []

    async def fake_fetch_json(url: str) -> dict[str, Any]:
        fetch_calls.append(url)
        if url == discovery:
            return {"jwks_uri": jwks_uri}
        if url == jwks_uri:
            if fetch_calls.count(jwks_uri) == 1:
                return {"keys": [signed_token_bundle["public_jwk"]]}
            return {"keys": [rotated_public]}
        raise AssertionError(url)

    monkeypatch.setattr("ato_service.oidc_jwt._fetch_json", fake_fetch_json)
    token = _issue_token(
        rotated_private,
        issuer=issuer,
        audience="ato-analyzer",
        nonce=signed_token_bundle["nonce"],
        kid="rotated-key",
    )
    settings = _settings(issuer=issuer)
    claims = _run(
        validate_production_id_token(
            id_token=token,
            settings=settings,
            nonce=signed_token_bundle["nonce"],
        )
    )
    assert claims["sub"] == "user-123"
    assert fetch_calls.count(jwks_uri) == 2


def test_validate_production_id_token_rejects_unknown_kid_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
    issuer_urls,
    signed_token_bundle,
) -> None:
    _patch_oidc_http(monkeypatch, issuer_urls=issuer_urls, public_jwk=signed_token_bundle["public_jwk"])
    token = _issue_token(
        signed_token_bundle["private_jwk"],
        issuer=signed_token_bundle["issuer"],
        audience="ato-analyzer",
        nonce=signed_token_bundle["nonce"],
        kid="missing-kid",
    )
    settings = _settings(issuer=signed_token_bundle["issuer"])
    with pytest.raises(OidcAuthenticationError):
        _run(
            validate_production_id_token(
                id_token=token,
                settings=settings,
                nonce=signed_token_bundle["nonce"],
            )
        )


def test_validate_production_id_token_rejects_jwk_header_fallback(
    monkeypatch: pytest.MonkeyPatch,
    issuer_urls,
    signed_token_bundle,
) -> None:
    _patch_oidc_http(monkeypatch, issuer_urls=issuer_urls, public_jwk=signed_token_bundle["public_jwk"])
    token = _issue_token(
        signed_token_bundle["private_jwk"],
        issuer=signed_token_bundle["issuer"],
        audience="ato-analyzer",
        nonce=signed_token_bundle["nonce"],
        include_jwk_header=True,
    )
    settings = _settings(issuer=signed_token_bundle["issuer"])
    with pytest.raises(OidcAuthenticationError):
        _run(
            validate_production_id_token(
                id_token=token,
                settings=settings,
                nonce=signed_token_bundle["nonce"],
            )
        )


def test_validate_production_id_token_rejects_none_algorithm(
    monkeypatch: pytest.MonkeyPatch,
    issuer_urls,
    signed_token_bundle,
) -> None:
    _patch_oidc_http(monkeypatch, issuer_urls=issuer_urls, public_jwk=signed_token_bundle["public_jwk"])
    token = _issue_token(
        signed_token_bundle["private_jwk"],
        issuer=signed_token_bundle["issuer"],
        audience="ato-analyzer",
        nonce=signed_token_bundle["nonce"],
        algorithm="none",
    )
    settings = _settings(issuer=signed_token_bundle["issuer"])
    with pytest.raises(OidcAuthenticationError):
        _run(
            validate_production_id_token(
                id_token=token,
                settings=settings,
                nonce=signed_token_bundle["nonce"],
            )
        )


def test_embedded_dev_oidc_isolated_to_dev_local() -> None:
    dev_settings = _settings(issuer=f"http://127.0.0.1:8000{DEV_OIDC_PATH_PREFIX}")
    prod_config = _runtime_config(profile="onprem_production", issuer=dev_settings.oidc_issuer_url)
    dev_config = _runtime_config(profile="dev_local", issuer=dev_settings.oidc_issuer_url)
    assert is_embedded_dev_oidc_issuer(dev_config, dev_settings) is True
    assert is_embedded_dev_oidc_issuer(prod_config, dev_settings) is False


def test_exchange_code_rejects_dev_issuer_in_production_profile() -> None:
    settings = _settings(issuer=f"https://portal.example{DEV_OIDC_PATH_PREFIX}")
    config = _runtime_config(profile="onprem_production", issuer=settings.oidc_issuer_url)
    with pytest.raises(OidcAuthenticationError):
        _run(
            exchange_code_for_identity(
                config=config,
                settings=settings,
                code="code",
                code_verifier="verifier",
                nonce="nonce",
            )
        )
