"""Tests for generic secret-byte credential resolution."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tests.support.platform import requires_symlink

from ato_service.credentials import (
    CREDENTIALS_DIRECTORY_ENV_VAR,
    CredentialResolutionError,
    read_secret_bytes_from_file,
    resolve_secret_bytes_from_credential_reference,
)

VALID_KEY = b"x" * 32
SECRET_KEY = b"super-secret-audit-key-material-32b"


def _posix_file_stat_result(
    *,
    uid: int = 0,
    mode: int = 0o100640,
) -> os.stat_result:
    return os.stat_result((mode, 0, 0, 0, uid, 0, 0, 0, 0, 0))


def _enable_posix_metadata_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ato_service.credentials.os.getuid", lambda: 0, raising=False)


def _install_lstat_mock(
    monkeypatch: pytest.MonkeyPatch,
    stat_result: os.stat_result,
) -> None:
    monkeypatch.setattr(Path, "lstat", lambda self: stat_result)


def test_read_secret_bytes_from_root_owned_file(tmp_path: Path) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(VALID_KEY)

    assert read_secret_bytes_from_file(key_file.resolve()) == VALID_KEY


def test_read_secret_bytes_rejects_relative_path(tmp_path: Path) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(VALID_KEY)

    with pytest.raises(CredentialResolutionError, match="absolute path"):
        read_secret_bytes_from_file(Path("relative/audit-hmac-key"))


def test_read_secret_bytes_rejects_empty_file(tmp_path: Path) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(b"")

    with pytest.raises(CredentialResolutionError, match="non-empty"):
        read_secret_bytes_from_file(key_file.resolve())


def test_read_secret_bytes_rejects_oversize_file(tmp_path: Path) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(b"x" * (64 * 1024 + 1))

    with pytest.raises(CredentialResolutionError, match="maximum secret size"):
        read_secret_bytes_from_file(key_file.resolve())


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        (stat.S_IFLNK | 0o777, "not a symlink"),
        (stat.S_IFDIR | 0o755, "regular file"),
    ],
)
def test_read_secret_bytes_rejects_non_regular_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    message: str,
) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(SECRET_KEY)
    _install_lstat_mock(monkeypatch, _posix_file_stat_result(mode=mode))

    with pytest.raises(CredentialResolutionError, match=message) as exc_info:
        read_secret_bytes_from_file(key_file.resolve())

    assert SECRET_KEY.decode() not in str(exc_info.value)


def test_read_secret_bytes_rejects_non_root_owner_when_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(SECRET_KEY)
    _enable_posix_metadata_checks(monkeypatch)
    _install_lstat_mock(
        monkeypatch,
        _posix_file_stat_result(uid=1000, mode=0o100640),
    )

    with pytest.raises(CredentialResolutionError, match="owned by root") as exc_info:
        read_secret_bytes_from_file(
            key_file.resolve(),
            enforce_root_owned_file_metadata=True,
        )

    assert SECRET_KEY.decode() not in str(exc_info.value)


@pytest.mark.parametrize("mode", [0o100640, 0o100600, 0o100400])
def test_read_secret_bytes_accepts_secure_root_owned_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(VALID_KEY)
    _enable_posix_metadata_checks(monkeypatch)
    _install_lstat_mock(monkeypatch, _posix_file_stat_result(mode=mode))

    assert (
        read_secret_bytes_from_file(
            key_file.resolve(),
            enforce_root_owned_file_metadata=True,
        )
        == VALID_KEY
    )


def test_read_secret_bytes_rejects_insecure_permissions_when_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(SECRET_KEY)
    _enable_posix_metadata_checks(monkeypatch)
    _install_lstat_mock(monkeypatch, _posix_file_stat_result(mode=0o100644))

    with pytest.raises(CredentialResolutionError, match="0640 or stricter"):
        read_secret_bytes_from_file(
            key_file.resolve(),
            enforce_root_owned_file_metadata=True,
        )


def test_resolve_secret_bytes_from_root_owned_file_reference(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(VALID_KEY)

    resolved = resolve_secret_bytes_from_credential_reference(
        {
            "source": "root_owned_file",
            "path": str(key_file.resolve()),
        }
    )

    assert resolved == VALID_KEY


def test_resolve_secret_bytes_from_systemd_credential_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    credential_file = cred_dir / "audit-hmac-key"
    credential_file.write_bytes(VALID_KEY)
    monkeypatch.setenv(CREDENTIALS_DIRECTORY_ENV_VAR, str(cred_dir.resolve()))

    resolved = resolve_secret_bytes_from_credential_reference(
        {
            "source": "systemd_credential",
            "identifier": "audit-hmac-key",
        }
    )

    assert resolved == VALID_KEY


@requires_symlink
def test_resolve_secret_bytes_rejects_symlinked_systemd_credential_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    outside_file = tmp_path / "outside-key"
    outside_file.write_bytes(VALID_KEY)
    monkeypatch.setenv(CREDENTIALS_DIRECTORY_ENV_VAR, str(cred_dir.resolve()))

    symlink_path = cred_dir / "audit-hmac-key"
    symlink_path.symlink_to(outside_file.resolve())

    with pytest.raises(
        CredentialResolutionError,
        match="within the credentials directory",
    ):
        resolve_secret_bytes_from_credential_reference(
            {
                "source": "systemd_credential",
                "identifier": "audit-hmac-key",
            }
        )


def test_resolve_secret_bytes_rejects_resolved_path_outside_credentials_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    outside_file = tmp_path / "outside-key"
    outside_file.write_bytes(VALID_KEY)
    monkeypatch.setenv(CREDENTIALS_DIRECTORY_ENV_VAR, str(cred_dir.resolve()))

    original_resolve = Path.resolve

    def fake_resolve(self: Path) -> Path:
        resolved = original_resolve(self)
        if self.name == "audit-hmac-key":
            return outside_file.resolve()
        return resolved

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    with pytest.raises(
        CredentialResolutionError,
        match="within the credentials directory",
    ):
        resolve_secret_bytes_from_credential_reference(
            {
                "source": "systemd_credential",
                "identifier": "audit-hmac-key",
            }
        )


def test_resolve_secret_bytes_rejects_malformed_systemd_identifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    monkeypatch.setenv(CREDENTIALS_DIRECTORY_ENV_VAR, str(cred_dir.resolve()))

    with pytest.raises(CredentialResolutionError, match="malformed"):
        resolve_secret_bytes_from_credential_reference(
            {
                "source": "systemd_credential",
                "identifier": "../outside",
            }
        )


@pytest.mark.parametrize(
    "reference",
    [
        {"source": "inline", "value": VALID_KEY},
        {"source": "systemd_credential"},
        {"source": "root_owned_file"},
        {"source": "systemd_credential", "identifier": ""},
        {"source": "root_owned_file", "path": ""},
    ],
)
def test_resolve_secret_bytes_rejects_malformed_credential_reference(
    reference: dict[str, object],
) -> None:
    with pytest.raises(CredentialResolutionError):
        resolve_secret_bytes_from_credential_reference(reference)  # type: ignore[arg-type]


def test_credential_resolution_errors_never_include_secret_contents(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(SECRET_KEY)
    missing_file = tmp_path / "missing-key"

    with pytest.raises(CredentialResolutionError) as exc_info:
        read_secret_bytes_from_file(missing_file.resolve())

    message = str(exc_info.value)
    assert SECRET_KEY.decode() not in message
    assert "super-secret" not in message
