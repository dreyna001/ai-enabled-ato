"""Focused tests for ordered audit-chain verification."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from ato_service.audit import (
    GENESIS_PREVIOUS_EVENT_HASH,
    MIN_AUDIT_HMAC_KEY_BYTES,
    compute_audit_event_hash,
)
from ato_service.audit_chain_verify import (
    AuditChainFailureReason,
    AuditChainVerifyOptions,
    redact_verification_detail,
    verify_audit_chain_events,
)
from ato_service.db.models import AuditEvent

UTC = timezone.utc
NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
HMAC_KEY = b"x" * MIN_AUDIT_HMAC_KEY_BYTES
WRONG_KEY = b"y" * MIN_AUDIT_HMAC_KEY_BYTES
EVENT_ID_ONE = uuid.UUID("11111111-1111-4111-8111-111111111111")
EVENT_ID_TWO = uuid.UUID("22222222-2222-4222-8222-222222222222")
EVENT_ID_THREE = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _make_event(
    *,
    audit_event_id: uuid.UUID,
    occurred_at: datetime,
    previous_event_hash: str,
    event_hash: str,
    action: str = "package_revision.created",
    object_type: str = "package_revision",
    object_id: str = "44444444-4444-4444-8444-444444444444",
    metadata: dict[str, object] | None = None,
) -> AuditEvent:
    return AuditEvent(
        audit_event_id=audit_event_id,
        occurred_at=occurred_at,
        actor_type="service",
        actor_id="analysis-worker",
        action=action,
        object_type=object_type,
        object_id=object_id,
        outcome="succeeded",
        reason_code=None,
        metadata_=metadata or {"request_id": "77777777-7777-4777-8777-777777777777"},
        previous_event_hash=previous_event_hash,
        event_hash=event_hash,
    )


def _build_chain(count: int) -> list[AuditEvent]:
    events: list[AuditEvent] = []
    previous_hash = GENESIS_PREVIOUS_EVENT_HASH
    for index in range(count):
        event_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"audit-event-{index}")
        occurred_at = NOW + timedelta(seconds=index)
        metadata = {"sequence": index}
        event_hash = compute_audit_event_hash(
            hmac_key=HMAC_KEY,
            audit_event_id=event_id,
            occurred_at=occurred_at,
            actor_type="service",
            actor_id="analysis-worker",
            action="package_revision.created",
            object_type="package_revision",
            object_id=f"{index:032x}",
            outcome="succeeded",
            reason_code=None,
            metadata=metadata,
            previous_event_hash=previous_hash,
        )
        events.append(
            _make_event(
                audit_event_id=event_id,
                occurred_at=occurred_at,
                previous_event_hash=previous_hash,
                event_hash=event_hash,
                object_id=f"{index:032x}",
                metadata=metadata,
            )
        )
        previous_hash = event_hash
    return events


def test_verify_empty_chain_passes() -> None:
    report = verify_audit_chain_events([], hmac_key=HMAC_KEY)

    assert report.passed is True
    assert report.verified_events == 0
    assert report.total_events == 0
    assert report.head_hash is None
    assert report.failure_reason is None


def test_verify_valid_chain_includes_root_and_checkpoints() -> None:
    events = _build_chain(3)
    report = verify_audit_chain_events(
        events,
        hmac_key=HMAC_KEY,
        options=AuditChainVerifyOptions(checkpoint_interval=2),
    )

    assert report.passed is True
    assert report.verified_events == 3
    assert report.genesis_hash == GENESIS_PREVIOUS_EVENT_HASH
    assert report.head_hash == events[-1].event_hash
    assert report.checkpoints[0].event_index == 0
    assert report.head_hash == events[-1].event_hash
    assert len(report.checkpoints) >= 2


def test_verify_detects_chain_break_on_missing_link() -> None:
    events = _build_chain(2)
    broken = _make_event(
        audit_event_id=EVENT_ID_THREE,
        occurred_at=NOW + timedelta(seconds=3),
        previous_event_hash="f" * 64,
        event_hash="a" * 64,
    )
    report = verify_audit_chain_events(events + [broken], hmac_key=HMAC_KEY)

    assert report.passed is False
    assert report.failure_reason is AuditChainFailureReason.CHAIN_BREAK
    assert report.failure_event_index == 2


def test_verify_detects_hmac_mismatch_tamper() -> None:
    events = _build_chain(2)
    tampered = events[1]
    tampered = _make_event(
        audit_event_id=tampered.audit_event_id,
        occurred_at=tampered.occurred_at,
        previous_event_hash=tampered.previous_event_hash,
        event_hash="b" * 64,
        action="package_revision.confirmed",
    )
    report = verify_audit_chain_events(
        [events[0], tampered],
        hmac_key=HMAC_KEY,
    )

    assert report.passed is False
    assert report.failure_reason is AuditChainFailureReason.HMAC_MISMATCH
    assert report.failure_event_index == 1


def test_verify_detects_wrong_key_at_genesis() -> None:
    events = _build_chain(1)
    report = verify_audit_chain_events(events, hmac_key=WRONG_KEY)

    assert report.passed is False
    assert report.failure_reason is AuditChainFailureReason.WRONG_KEY
    assert report.failure_event_index == 0


def test_verify_detects_deletion_gap_as_chain_break() -> None:
    events = _build_chain(3)
    report = verify_audit_chain_events([events[0], events[2]], hmac_key=HMAC_KEY)

    assert report.passed is False
    assert report.failure_reason is AuditChainFailureReason.CHAIN_BREAK
    assert report.failure_event_index == 1


def test_verify_detects_reordered_events() -> None:
    event0_id = uuid.uuid5(uuid.NAMESPACE_DNS, "audit-event-late")
    event1_id = uuid.uuid5(uuid.NAMESPACE_DNS, "audit-event-early")
    event0_hash = compute_audit_event_hash(
        hmac_key=HMAC_KEY,
        audit_event_id=event0_id,
        occurred_at=NOW + timedelta(seconds=2),
        actor_type="service",
        actor_id="analysis-worker",
        action="package_revision.created",
        object_type="package_revision",
        object_id="00000000000000000000000000000001",
        outcome="succeeded",
        reason_code=None,
        metadata={"sequence": 0},
        previous_event_hash=GENESIS_PREVIOUS_EVENT_HASH,
    )
    event1_hash = compute_audit_event_hash(
        hmac_key=HMAC_KEY,
        audit_event_id=event1_id,
        occurred_at=NOW + timedelta(seconds=1),
        actor_type="service",
        actor_id="analysis-worker",
        action="package_revision.created",
        object_type="package_revision",
        object_id="00000000000000000000000000000002",
        outcome="succeeded",
        reason_code=None,
        metadata={"sequence": 1},
        previous_event_hash=event0_hash,
    )
    events = [
        _make_event(
            audit_event_id=event0_id,
            occurred_at=NOW + timedelta(seconds=2),
            previous_event_hash=GENESIS_PREVIOUS_EVENT_HASH,
            event_hash=event0_hash,
            object_id="00000000000000000000000000000001",
            metadata={"sequence": 0},
        ),
        _make_event(
            audit_event_id=event1_id,
            occurred_at=NOW + timedelta(seconds=1),
            previous_event_hash=event0_hash,
            event_hash=event1_hash,
            object_id="00000000000000000000000000000002",
            metadata={"sequence": 1},
        ),
    ]
    report = verify_audit_chain_events(events, hmac_key=HMAC_KEY)

    assert report.passed is False
    assert report.failure_reason is AuditChainFailureReason.ORDERING_VIOLATION
    assert report.failure_event_index == 1


def test_filter_summary_does_not_change_global_passed() -> None:
    events = _build_chain(1)
    event1_id = uuid.uuid5(uuid.NAMESPACE_DNS, "audit-event-system")
    event1_hash = compute_audit_event_hash(
        hmac_key=HMAC_KEY,
        audit_event_id=event1_id,
        occurred_at=NOW + timedelta(seconds=1),
        actor_type="service",
        actor_id="analysis-worker",
        action="package_revision.created",
        object_type="system",
        object_id="99999999-9999-4999-8999-999999999999",
        outcome="succeeded",
        reason_code=None,
        metadata={"sequence": 1},
        previous_event_hash=events[0].event_hash,
    )
    events.append(
        _make_event(
            audit_event_id=event1_id,
            occurred_at=NOW + timedelta(seconds=1),
            previous_event_hash=events[0].event_hash,
            event_hash=event1_hash,
            object_type="system",
            object_id="99999999-9999-4999-8999-999999999999",
            metadata={"sequence": 1},
        )
    )
    report = verify_audit_chain_events(
        events,
        hmac_key=HMAC_KEY,
        options=AuditChainVerifyOptions(object_type="system"),
    )

    assert report.passed is True
    assert report.matching_events == 1
    assert report.verification_scope == "global"


def test_redacted_output_omits_metadata_secrets() -> None:
    events = _build_chain(1)
    events[0].metadata_ = {"request_id": "77777777-7777-4777-8777-777777777777"}
    report = verify_audit_chain_events(events, hmac_key=HMAC_KEY)
    payload = report.to_redacted_dict()

    assert "sk-live-secret-token" not in str(payload)
    assert HMAC_KEY.decode("ascii") not in str(payload)
    redact_verification_detail(payload)


def test_redacted_output_rejects_secret_like_report_material() -> None:
    with pytest.raises(ValueError, match="secret-like"):
        redact_verification_detail({"detail": "Bearer leaked-token"})


def test_compute_hash_round_trip_used_by_verifier() -> None:
    event_hash = compute_audit_event_hash(
        hmac_key=HMAC_KEY,
        audit_event_id=EVENT_ID_ONE,
        occurred_at=NOW,
        actor_type="service",
        actor_id="analysis-worker",
        action="package_revision.created",
        object_type="package_revision",
        object_id="44444444-4444-4444-8444-444444444444",
        outcome="succeeded",
        reason_code=None,
        metadata={"request_id": "77777777-7777-4777-8777-777777777777"},
        previous_event_hash=GENESIS_PREVIOUS_EVENT_HASH,
    )
    event = _make_event(
        audit_event_id=EVENT_ID_ONE,
        occurred_at=NOW,
        previous_event_hash=GENESIS_PREVIOUS_EVENT_HASH,
        event_hash=event_hash,
    )
    report = verify_audit_chain_events([event], hmac_key=HMAC_KEY)

    assert report.passed is True
    assert report.head_hash == event_hash
