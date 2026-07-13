"""Local ClamAV clamd adapter for on-prem malware scanning (Component A Diff 7)."""

from __future__ import annotations

import ipaddress
import socket
import struct
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ato_service.malware_scan import MalwareScanOutcome, MalwareScanResult
from ato_service.runtime_config import RuntimeConfig, RuntimeConfigError

INSTREAM_CHUNK_SIZE = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 30


class ClamAvTransport(StrEnum):
    UNIX_SOCKET = "unix_socket"
    TCP_LOOPBACK = "tcp_loopback"


@dataclass(frozen=True, slots=True)
class ClamAvScannerSettings:
    transport: ClamAvTransport
    socket_path: Path | None
    host: str | None
    port: int | None
    timeout_seconds: float


class ClamAvConfigurationError(RuntimeConfigError):
    error_code = "malware_scan_unavailable"


def _positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ClamAvConfigurationError(f"{field_name} must be a positive integer")
    return value


def _approved_tcp_host(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ClamAvConfigurationError("MALWARE_SCANNER_HOST must be a valid IP address") from exc
    if not (address.is_loopback or address.is_private):
        raise ClamAvConfigurationError(
            "MALWARE_SCANNER_HOST must be loopback or private internal address"
        )


def resolve_clamav_scanner_settings(config: RuntimeConfig) -> ClamAvScannerSettings:
    """Parse and validate ClamAV transport settings from runtime JSON."""
    document = config.document
    transport_raw = document.get("MALWARE_SCANNER_TRANSPORT")
    if transport_raw not in {ClamAvTransport.UNIX_SOCKET, ClamAvTransport.TCP_LOOPBACK}:
        raise ClamAvConfigurationError(
            "MALWARE_SCANNER_TRANSPORT must be unix_socket or tcp_loopback"
        )
    transport = ClamAvTransport(str(transport_raw))
    timeout_seconds = float(
        _positive_int(
            document.get("MALWARE_SCANNER_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
            field_name="MALWARE_SCANNER_TIMEOUT_SECONDS",
        )
    )
    if transport == ClamAvTransport.UNIX_SOCKET:
        socket_path_raw = document.get("MALWARE_SCANNER_SOCKET_PATH")
        if not isinstance(socket_path_raw, str) or not socket_path_raw.strip():
            raise ClamAvConfigurationError("MALWARE_SCANNER_SOCKET_PATH is required")
        return ClamAvScannerSettings(
            transport=transport,
            socket_path=Path(socket_path_raw.strip()),
            host=None,
            port=None,
            timeout_seconds=timeout_seconds,
        )
    host_raw = document.get("MALWARE_SCANNER_HOST")
    if not isinstance(host_raw, str) or not host_raw.strip():
        raise ClamAvConfigurationError("MALWARE_SCANNER_HOST is required for tcp_loopback")
    host = host_raw.strip()
    _approved_tcp_host(host)
    port = _positive_int(
        document.get("MALWARE_SCANNER_PORT", 3310),
        field_name="MALWARE_SCANNER_PORT",
    )
    return ClamAvScannerSettings(
        transport=transport,
        socket_path=None,
        host=host,
        port=port,
        timeout_seconds=timeout_seconds,
    )


class ClamAvMalwareScanner:
    """Scan verified artifact bytes through a local clamd daemon."""

    def __init__(self, settings: ClamAvScannerSettings) -> None:
        self._settings = settings

    def scan_verified_bytes(
        self,
        *,
        content_bytes: bytes,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> MalwareScanResult:
        del expected_sha256
        if expected_size_bytes < 1 or len(content_bytes) != expected_size_bytes:
            return MalwareScanResult(
                MalwareScanOutcome.ERROR,
                reason_code="source_type_mismatch",
            )
        try:
            response = self._instream_scan(content_bytes)
        except OSError:
            return MalwareScanResult(
                MalwareScanOutcome.ERROR,
                reason_code="scanner_unavailable",
            )
        except TimeoutError:
            return MalwareScanResult(
                MalwareScanOutcome.ERROR,
                reason_code="scanner_timeout",
            )
        normalized = response.strip()
        if normalized.endswith("OK"):
            return MalwareScanResult(MalwareScanOutcome.CLEAN)
        if "FOUND" in normalized:
            return MalwareScanResult(
                MalwareScanOutcome.INFECTED,
                reason_code="malware_detected",
            )
        return MalwareScanResult(
            MalwareScanOutcome.ERROR,
            reason_code="scanner_protocol_error",
        )

    def scan_stored_artifact(
        self,
        *,
        blob_store: object,
        artifact: object,
    ) -> MalwareScanResult:
        from ato_service.blobs import BlobStore
        from ato_service.db.models import SourceArtifact
        from ato_service.source_artifacts import (
            SourceArtifactStorageError,
            SourceTypeMismatchError,
            read_source_artifact_bytes,
        )

        if not isinstance(blob_store, BlobStore) or not isinstance(artifact, SourceArtifact):
            return MalwareScanResult(
                MalwareScanOutcome.ERROR,
                reason_code="source_type_mismatch",
            )
        try:
            content_bytes = read_source_artifact_bytes(blob_store, artifact)
        except SourceArtifactStorageError:
            return MalwareScanResult(
                MalwareScanOutcome.ERROR,
                reason_code="storage_unavailable",
            )
        except SourceTypeMismatchError:
            return MalwareScanResult(
                MalwareScanOutcome.ERROR,
                reason_code="source_type_mismatch",
            )
        return self.scan_verified_bytes(
            content_bytes=content_bytes,
            expected_sha256=artifact.sha256,
            expected_size_bytes=artifact.size_bytes,
        )

    def _connect(self) -> socket.socket:
        settings = self._settings
        if settings.transport == ClamAvTransport.UNIX_SOCKET:
            assert settings.socket_path is not None
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(settings.timeout_seconds)
            client.connect(str(settings.socket_path))
            return client
        assert settings.host is not None and settings.port is not None
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(settings.timeout_seconds)
        client.connect((settings.host, settings.port))
        return client

    def _instream_scan(self, content_bytes: bytes) -> str:
        client = self._connect()
        try:
            client.sendall(b"zINSTREAM\0")
            offset = 0
            while offset < len(content_bytes):
                chunk = content_bytes[offset : offset + INSTREAM_CHUNK_SIZE]
                client.sendall(struct.pack("!L", len(chunk)) + chunk)
                offset += len(chunk)
            client.sendall(struct.pack("!L", 0))
            response = _recv_until_null(client)
        finally:
            client.close()
        decoded = response.decode("utf-8", errors="replace")
        return decoded


def _recv_until_null(client: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        part = client.recv(4096)
        if not part:
            break
        chunks.append(part)
        if b"\0" in part:
            break
    return b"".join(chunks).split(b"\0", 1)[0]


__all__ = [
    "ClamAvConfigurationError",
    "ClamAvMalwareScanner",
    "ClamAvScannerSettings",
    "ClamAvTransport",
    "resolve_clamav_scanner_settings",
]
