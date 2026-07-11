"""Deterministic lifecycle transition guards for domain state machines."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PackageRevisionStatus(StrEnum):
    UPLOADING = "uploading"
    SCANNING = "scanning"
    EXTRACTING = "extracting"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    READY = "ready"
    INVALID = "invalid"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"


class PackageRevisionTransitionCondition(StrEnum):
    NORMAL_PROGRESSION = "normal_progression"
    INVALID_CUSTOMER_INPUT_BEFORE_SCAN = "invalid_customer_input_before_scan"
    INVALID_CONTENT_NOT_MALWARE = "invalid_content_not_malware"
    INVALID_EXTRACTION_OR_REFERENCE = "invalid_extraction_or_reference"
    MALWARE_SCANNER_INFECTED = "malware_scanner_infected"
    AUTHORIZED_ARCHIVE = "authorized_archive"


class AnalysisRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    POLICY_BLOCKED = "policy_blocked"


class AnalysisRunTransitionCondition(StrEnum):
    WORKER_CLAIMED = "worker_claimed"
    OUTPUTS_COMMITTED = "outputs_committed"
    AUTHORIZED_CANCELLATION = "authorized_cancellation"
    POLICY_DENIED_BEFORE_EXECUTION = "policy_denied_before_execution"
    POLICY_DENIED_BEFORE_MODEL = "policy_denied_before_model"
    RUN_FAILED = "run_failed"


_PACKAGE_REVISION_TERMINAL = frozenset(
    {
        PackageRevisionStatus.INVALID,
        PackageRevisionStatus.QUARANTINED,
        PackageRevisionStatus.ARCHIVED,
    }
)

_ANALYSIS_RUN_TERMINAL = frozenset(
    {
        AnalysisRunStatus.SUCCEEDED,
        AnalysisRunStatus.FAILED,
        AnalysisRunStatus.CANCELLED,
        AnalysisRunStatus.POLICY_BLOCKED,
    }
)

_LEGAL_PACKAGE_REVISION_TRANSITIONS: frozenset[
    tuple[PackageRevisionStatus, PackageRevisionStatus, PackageRevisionTransitionCondition]
] = frozenset(
    {
        (
            PackageRevisionStatus.UPLOADING,
            PackageRevisionStatus.SCANNING,
            PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
        ),
        (
            PackageRevisionStatus.SCANNING,
            PackageRevisionStatus.EXTRACTING,
            PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
        ),
        (
            PackageRevisionStatus.EXTRACTING,
            PackageRevisionStatus.AWAITING_CONFIRMATION,
            PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
        ),
        (
            PackageRevisionStatus.AWAITING_CONFIRMATION,
            PackageRevisionStatus.READY,
            PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
        ),
        (
            PackageRevisionStatus.UPLOADING,
            PackageRevisionStatus.INVALID,
            PackageRevisionTransitionCondition.INVALID_CUSTOMER_INPUT_BEFORE_SCAN,
        ),
        (
            PackageRevisionStatus.SCANNING,
            PackageRevisionStatus.INVALID,
            PackageRevisionTransitionCondition.INVALID_CONTENT_NOT_MALWARE,
        ),
        (
            PackageRevisionStatus.EXTRACTING,
            PackageRevisionStatus.INVALID,
            PackageRevisionTransitionCondition.INVALID_EXTRACTION_OR_REFERENCE,
        ),
        (
            PackageRevisionStatus.SCANNING,
            PackageRevisionStatus.QUARANTINED,
            PackageRevisionTransitionCondition.MALWARE_SCANNER_INFECTED,
        ),
        (
            PackageRevisionStatus.UPLOADING,
            PackageRevisionStatus.ARCHIVED,
            PackageRevisionTransitionCondition.AUTHORIZED_ARCHIVE,
        ),
        (
            PackageRevisionStatus.SCANNING,
            PackageRevisionStatus.ARCHIVED,
            PackageRevisionTransitionCondition.AUTHORIZED_ARCHIVE,
        ),
        (
            PackageRevisionStatus.EXTRACTING,
            PackageRevisionStatus.ARCHIVED,
            PackageRevisionTransitionCondition.AUTHORIZED_ARCHIVE,
        ),
        (
            PackageRevisionStatus.AWAITING_CONFIRMATION,
            PackageRevisionStatus.ARCHIVED,
            PackageRevisionTransitionCondition.AUTHORIZED_ARCHIVE,
        ),
        (
            PackageRevisionStatus.READY,
            PackageRevisionStatus.ARCHIVED,
            PackageRevisionTransitionCondition.AUTHORIZED_ARCHIVE,
        ),
    }
)

_LEGAL_ANALYSIS_RUN_TRANSITIONS: frozenset[
    tuple[AnalysisRunStatus, AnalysisRunStatus, AnalysisRunTransitionCondition]
] = frozenset(
    {
        (
            AnalysisRunStatus.QUEUED,
            AnalysisRunStatus.RUNNING,
            AnalysisRunTransitionCondition.WORKER_CLAIMED,
        ),
        (
            AnalysisRunStatus.RUNNING,
            AnalysisRunStatus.SUCCEEDED,
            AnalysisRunTransitionCondition.OUTPUTS_COMMITTED,
        ),
        (
            AnalysisRunStatus.QUEUED,
            AnalysisRunStatus.CANCELLED,
            AnalysisRunTransitionCondition.AUTHORIZED_CANCELLATION,
        ),
        (
            AnalysisRunStatus.RUNNING,
            AnalysisRunStatus.CANCELLED,
            AnalysisRunTransitionCondition.AUTHORIZED_CANCELLATION,
        ),
        (
            AnalysisRunStatus.QUEUED,
            AnalysisRunStatus.POLICY_BLOCKED,
            AnalysisRunTransitionCondition.POLICY_DENIED_BEFORE_EXECUTION,
        ),
        (
            AnalysisRunStatus.RUNNING,
            AnalysisRunStatus.POLICY_BLOCKED,
            AnalysisRunTransitionCondition.POLICY_DENIED_BEFORE_MODEL,
        ),
        (
            AnalysisRunStatus.RUNNING,
            AnalysisRunStatus.FAILED,
            AnalysisRunTransitionCondition.RUN_FAILED,
        ),
    }
)


@dataclass(frozen=True, slots=True)
class IllegalStateTransitionError(Exception):
    """Raised when a requested lifecycle transition is not legal."""

    error_code: str
    current_state: str
    target_state: str
    condition: str | None = None

    def __post_init__(self) -> None:
        if self.error_code != "illegal_state_transition":
            raise ValueError("error_code must be illegal_state_transition")

    def __str__(self) -> str:
        if self.condition is None:
            return (
                f"illegal transition from {self.current_state!r} to "
                f"{self.target_state!r}"
            )
        return (
            f"illegal transition from {self.current_state!r} to "
            f"{self.target_state!r} with condition {self.condition!r}"
        )


def package_revision_status_is_terminal(status: PackageRevisionStatus) -> bool:
    return status in _PACKAGE_REVISION_TERMINAL


def analysis_run_status_is_terminal(status: AnalysisRunStatus) -> bool:
    return status in _ANALYSIS_RUN_TERMINAL


def is_legal_package_revision_transition(
    current: PackageRevisionStatus,
    target: PackageRevisionStatus,
    *,
    condition: PackageRevisionTransitionCondition,
) -> bool:
    return (current, target, condition) in _LEGAL_PACKAGE_REVISION_TRANSITIONS


def is_legal_analysis_run_transition(
    current: AnalysisRunStatus,
    target: AnalysisRunStatus,
    *,
    condition: AnalysisRunTransitionCondition,
) -> bool:
    return (current, target, condition) in _LEGAL_ANALYSIS_RUN_TRANSITIONS


def require_package_revision_transition(
    current: PackageRevisionStatus,
    target: PackageRevisionStatus,
    *,
    condition: PackageRevisionTransitionCondition,
) -> None:
    """Validate a PackageRevision transition or raise IllegalStateTransitionError."""
    if is_legal_package_revision_transition(current, target, condition=condition):
        return
    raise IllegalStateTransitionError(
        error_code="illegal_state_transition",
        current_state=current.value,
        target_state=target.value,
        condition=condition.value,
    )


def require_analysis_run_transition(
    current: AnalysisRunStatus,
    target: AnalysisRunStatus,
    *,
    condition: AnalysisRunTransitionCondition,
) -> None:
    """Validate an AnalysisRun transition or raise IllegalStateTransitionError."""
    if is_legal_analysis_run_transition(current, target, condition=condition):
        return
    raise IllegalStateTransitionError(
        error_code="illegal_state_transition",
        current_state=current.value,
        target_state=target.value,
        condition=condition.value,
    )
