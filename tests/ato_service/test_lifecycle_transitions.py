"""Tests for deterministic lifecycle transition guards."""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import pytest

from ato_service.lifecycle_transitions import (
    AnalysisRunStatus,
    AnalysisRunTransitionCondition,
    IllegalStateTransitionError,
    PackageRevisionStatus,
    PackageRevisionTransitionCondition,
    analysis_run_status_is_terminal,
    is_legal_analysis_run_transition,
    is_legal_package_revision_transition,
    package_revision_status_is_terminal,
    require_analysis_run_transition,
    require_package_revision_transition,
)


ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"


LEGAL_PACKAGE_REVISION_TRANSITIONS = [
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
    *[
        (
            source,
            PackageRevisionStatus.ARCHIVED,
            PackageRevisionTransitionCondition.AUTHORIZED_ARCHIVE,
        )
        for source in (
            PackageRevisionStatus.UPLOADING,
            PackageRevisionStatus.SCANNING,
            PackageRevisionStatus.EXTRACTING,
            PackageRevisionStatus.AWAITING_CONFIRMATION,
            PackageRevisionStatus.READY,
        )
    ],
]

LEGAL_ANALYSIS_RUN_TRANSITIONS = [
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
]


@pytest.mark.parametrize(
    ("current", "target", "condition"),
    LEGAL_PACKAGE_REVISION_TRANSITIONS,
)
def test_legal_package_revision_transitions(
    current: PackageRevisionStatus,
    target: PackageRevisionStatus,
    condition: PackageRevisionTransitionCondition,
) -> None:
    require_package_revision_transition(current, target, condition=condition)
    assert is_legal_package_revision_transition(current, target, condition=condition)


@pytest.mark.parametrize(
    ("current", "target", "condition"),
    LEGAL_ANALYSIS_RUN_TRANSITIONS,
)
def test_legal_analysis_run_transitions(
    current: AnalysisRunStatus,
    target: AnalysisRunStatus,
    condition: AnalysisRunTransitionCondition,
) -> None:
    require_analysis_run_transition(current, target, condition=condition)
    assert is_legal_analysis_run_transition(current, target, condition=condition)


@pytest.mark.parametrize(
    "terminal_status",
    [
        PackageRevisionStatus.INVALID,
        PackageRevisionStatus.QUARANTINED,
        PackageRevisionStatus.ARCHIVED,
    ],
)
def test_terminal_package_revision_states_have_no_outgoing_transition(
    terminal_status: PackageRevisionStatus,
) -> None:
    assert package_revision_status_is_terminal(terminal_status)
    for target in PackageRevisionStatus:
        for condition in PackageRevisionTransitionCondition:
            assert not is_legal_package_revision_transition(
                terminal_status,
                target,
                condition=condition,
            )


@pytest.mark.parametrize(
    "terminal_status",
    [
        AnalysisRunStatus.SUCCEEDED,
        AnalysisRunStatus.FAILED,
        AnalysisRunStatus.CANCELLED,
        AnalysisRunStatus.POLICY_BLOCKED,
    ],
)
def test_terminal_analysis_run_states_have_no_outgoing_transition(
    terminal_status: AnalysisRunStatus,
) -> None:
    assert analysis_run_status_is_terminal(terminal_status)
    for target in AnalysisRunStatus:
        for condition in AnalysisRunTransitionCondition:
            assert not is_legal_analysis_run_transition(
                terminal_status,
                target,
                condition=condition,
            )


def test_queued_to_succeeded_is_denied() -> None:
    with pytest.raises(IllegalStateTransitionError) as exc_info:
        require_analysis_run_transition(
            AnalysisRunStatus.QUEUED,
            AnalysisRunStatus.SUCCEEDED,
            condition=AnalysisRunTransitionCondition.OUTPUTS_COMMITTED,
        )
    assert exc_info.value.error_code == "illegal_state_transition"


def test_queued_to_failed_is_denied() -> None:
    with pytest.raises(IllegalStateTransitionError) as exc_info:
        require_analysis_run_transition(
            AnalysisRunStatus.QUEUED,
            AnalysisRunStatus.FAILED,
            condition=AnalysisRunTransitionCondition.RUN_FAILED,
        )
    assert exc_info.value.error_code == "illegal_state_transition"


def test_ready_cannot_return_to_active_intake_states() -> None:
    for target in (
        PackageRevisionStatus.UPLOADING,
        PackageRevisionStatus.SCANNING,
        PackageRevisionStatus.EXTRACTING,
        PackageRevisionStatus.AWAITING_CONFIRMATION,
    ):
        with pytest.raises(IllegalStateTransitionError):
            require_package_revision_transition(
                PackageRevisionStatus.READY,
                target,
                condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
            )


def test_scanning_invalid_and_quarantine_conditions_remain_distinct() -> None:
    with pytest.raises(IllegalStateTransitionError):
        require_package_revision_transition(
            PackageRevisionStatus.SCANNING,
            PackageRevisionStatus.INVALID,
            condition=PackageRevisionTransitionCondition.MALWARE_SCANNER_INFECTED,
        )
    with pytest.raises(IllegalStateTransitionError):
        require_package_revision_transition(
            PackageRevisionStatus.SCANNING,
            PackageRevisionStatus.QUARANTINED,
            condition=PackageRevisionTransitionCondition.INVALID_CONTENT_NOT_MALWARE,
        )


def test_illegal_state_transition_error_is_immutable_and_typed() -> None:
    with pytest.raises(IllegalStateTransitionError) as exc_info:
        require_package_revision_transition(
            PackageRevisionStatus.READY,
            PackageRevisionStatus.SCANNING,
            condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION,
        )

    error = exc_info.value
    assert error.error_code == "illegal_state_transition"
    assert error.current_state == "ready"
    assert error.target_state == "scanning"
    assert error.condition == "normal_progression"

    with pytest.raises(ValueError, match="error_code must be illegal_state_transition"):
        IllegalStateTransitionError(
            error_code="other_code",
            current_state="ready",
            target_state="scanning",
        )


def test_domain_schema_enum_values_match_python_strenum() -> None:
    schema = json.loads(DOMAIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema["$defs"]

    assert [member.value for member in PackageRevisionStatus] == defs["PackageRevision"][
        "properties"
    ]["status"]["enum"]
    assert [member.value for member in AnalysisRunStatus] == defs["AnalysisRun"][
        "properties"
    ]["status"]["enum"]


def test_no_unlisted_package_revision_transition_is_accidentally_legal() -> None:
    listed = {
        (current, target, condition)
        for current, target, condition in LEGAL_PACKAGE_REVISION_TRANSITIONS
    }
    for current, target, condition in product(
        PackageRevisionStatus,
        PackageRevisionStatus,
        PackageRevisionTransitionCondition,
    ):
        legal = is_legal_package_revision_transition(
            current,
            target,
            condition=condition,
        )
        assert legal == ((current, target, condition) in listed)


def test_no_unlisted_analysis_run_transition_is_accidentally_legal() -> None:
    listed = {
        (current, target, condition)
        for current, target, condition in LEGAL_ANALYSIS_RUN_TRANSITIONS
    }
    for current, target, condition in product(
        AnalysisRunStatus,
        AnalysisRunStatus,
        AnalysisRunTransitionCondition,
    ):
        legal = is_legal_analysis_run_transition(
            current,
            target,
            condition=condition,
        )
        assert legal == ((current, target, condition) in listed)
