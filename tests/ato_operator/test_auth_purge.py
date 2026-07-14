"""Operator auth purge command tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ato_operator.auth_purge import AuthPurgeReport, purge_expired_auth_artifacts_sync


def test_purge_expired_auth_artifacts_sync_returns_counts() -> None:
    config = MagicMock()
    expected = AuthPurgeReport(
        sessions_purged=4,
        login_states_purged=2,
        now="2026-07-14T12:00:00Z",
    )

    with patch(
        "ato_operator.auth_purge.resolve_runtime_database_dsn",
        return_value="postgresql+asyncpg://ato:secret@localhost/ato",
    ), patch(
        "ato_operator.auth_purge.resolve_runtime_audit_hmac_key",
        return_value=b"x" * 32,
    ), patch(
        "ato_operator.auth_purge.asyncio.run",
        return_value=expected,
    ):
        report = purge_expired_auth_artifacts_sync(config)

    assert report.sessions_purged == 4
    assert report.login_states_purged == 2
