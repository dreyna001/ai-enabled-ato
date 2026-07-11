"""Focused tests for authorized Systems persistence."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.db.models import AuditEvent, IdempotencyRecord, System
from ato_service.domain_mapping import map_system_to_domain
from ato_service.idempotency import request_digest_from_payload
from ato_service.pagination import encode_pagination_cursor
from ato_service.systems import (
    SYSTEMS_CREATE_OPERATION,
    CreateSystemResult,
    RequestSchemaInvalidError,
    ResourceNotFoundError,
    SystemsPage,
    _list_systems_select_statement,
    _system_read_access_predicate,
    create_system,
    create_system_request_digest_payload,
    get_system,
    list_systems,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)
HMAC_KEY = b"k" * MIN_AUDIT_HMAC_KEY_BYTES
IDEMPOTENCY_KEY = "idem-key-0123456789"
OWNER_GROUP = "owners"
VIEWER_GROUPS = ["viewers"]
SYSTEM_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_SYSTEM_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _compile_sql(statement: object, *, literal_binds: bool = True) -> str:
    kwargs: dict[str, Any] = {}
    if literal_binds:
        kwargs["literal_binds"] = True
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs=kwargs,
        )
    )


def _principal(
    *,
    actor_id: str = "operator@example.test",
    groups: tuple[str, ...] = (OWNER_GROUP,),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id=actor_id,
        groups=groups,
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example",),
    )


def _make_system(
    *,
    system_id: uuid.UUID = SYSTEM_ID,
    owner_group: str = OWNER_GROUP,
    viewer_groups: list[str] | None = None,
    created_at: datetime = NOW,
) -> System:
    return System(
        system_id=system_id,
        display_name="Example System",
        external_system_id="EXT-1",
        owner_group=owner_group,
        viewer_groups=viewer_groups if viewer_groups is not None else list(VIEWER_GROUPS),
        created_at=created_at,
        archived_at=None,
    )


class _RecordingSession:
    def __init__(self, execute_results: list[MagicMock] | None = None) -> None:
        self._execute_results = list(execute_results or [])
        self.added: list[object] = []
        self.execute_calls: list[object] = []

    async def execute(self, statement: object) -> MagicMock:
        self.execute_calls.append(statement)
        if not self._execute_results:
            raise AssertionError("unexpected execute call")
        return self._execute_results.pop(0)

    def add(self, obj: object) -> None:
        self.added.append(obj)


def _scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def _digest_payload() -> dict[str, Any]:
    return create_system_request_digest_payload(
        display_name="Example System",
        external_system_id="EXT-1",
        owner_group=OWNER_GROUP,
        viewer_groups=VIEWER_GROUPS,
    )


def test_create_system_request_digest_excludes_secret_session_and_csrf_fields() -> None:
    payload = _digest_payload()
    assert set(payload.keys()) == {
        "display_name",
        "external_system_id",
        "owner_group",
        "viewer_groups",
    }
    assert "csrf_token" not in payload
    assert "session" not in payload
    assert "secret" not in payload


def test_system_read_access_predicate_compiles_owner_and_viewer_jsonb_checks() -> None:
    sql = _compile_sql(_system_read_access_predicate(("owners", "viewers")), literal_binds=False)
    assert "systems.owner_group IN" in sql
    assert "viewer_groups @>" in sql
    assert " OR " in sql


def test_list_systems_sql_filters_in_database_with_stable_cursor_and_limit_plus_one() -> None:
    cursor = encode_pagination_cursor(NOW, SYSTEM_ID)
    from ato_service.pagination import decode_pagination_cursor

    decoded = decode_pagination_cursor(cursor)
    statement = _list_systems_select_statement(
        principal_groups=("owners", "viewers"),
        cursor=decoded,
        limit=50,
    )
    sql = _compile_sql(statement, literal_binds=False)

    assert "FROM systems" in sql
    assert "systems.owner_group IN" in sql
    assert "viewer_groups @>" in sql
    assert "systems.created_at >" in sql
    assert "systems.created_at =" in sql
    assert "systems.system_id >" in sql
    assert "ORDER BY systems.created_at ASC, systems.system_id ASC" in sql
    assert statement._limit == 51  # type: ignore[attr-defined]


def test_list_systems_sql_without_cursor_orders_and_limits() -> None:
    statement = _list_systems_select_statement(
        principal_groups=("owners",),
        cursor=None,
        limit=25,
    )
    sql = _compile_sql(statement, literal_binds=False)
    assert "systems.created_at >" not in sql
    assert "ORDER BY systems.created_at ASC, systems.system_id ASC" in sql
    assert statement._limit == 26  # type: ignore[attr-defined]


def test_create_system_rejects_invalid_request_schema() -> None:
    session = _RecordingSession()

    with pytest.raises(RequestSchemaInvalidError) as exc_info:
        _run(
            create_system(
                session,
                principal=_principal(),
                audit_hmac_key=HMAC_KEY,
                idempotency_key=IDEMPOTENCY_KEY,
                display_name="",
                external_system_id="EXT-1",
                owner_group=OWNER_GROUP,
                viewer_groups=VIEWER_GROUPS,
                now=NOW,
            )
        )

    assert exc_info.value.error_code == "request_schema_invalid"
    assert any(error.path == "display_name" for error in exc_info.value.field_errors)
    assert session.added == []
    assert session.execute_calls == []


def test_create_system_denies_non_owner_before_idempotency_lookup() -> None:
    session = _RecordingSession()

    with pytest.raises(AuthorizationDeniedError) as exc_info:
        _run(
            create_system(
                session,
                principal=_principal(groups=("viewers",)),
                audit_hmac_key=HMAC_KEY,
                idempotency_key=IDEMPOTENCY_KEY,
                display_name="Example System",
                external_system_id="EXT-1",
                owner_group=OWNER_GROUP,
                viewer_groups=VIEWER_GROUPS,
                now=NOW,
            )
        )

    assert exc_info.value.error_code == "authorization_denied"
    assert OWNER_GROUP not in str(exc_info.value)
    assert session.execute_calls == []


def test_create_system_inserts_audit_and_idempotency_on_success() -> None:
    session = _RecordingSession(
        [
            MagicMock(),
            _scalar_result(None),
            MagicMock(),
            _scalar_result(None),
        ]
    )

    result = _run(
        create_system(
            session,
            principal=_principal(),
            audit_hmac_key=HMAC_KEY,
            idempotency_key=IDEMPOTENCY_KEY,
            display_name="Example System",
            external_system_id="EXT-1",
            owner_group=OWNER_GROUP,
            viewer_groups=VIEWER_GROUPS,
            now=NOW,
        )
    )

    assert isinstance(result, CreateSystemResult)
    assert result.status == 201
    assert result.replayed is False
    assert result.payload["object_type"] == "system"
    assert result.payload["display_name"] == "Example System"
    assert result.payload["archived_at"] is None

    systems = [obj for obj in session.added if isinstance(obj, System)]
    audit_events = [obj for obj in session.added if isinstance(obj, AuditEvent)]
    assert len(systems) == 1
    assert len(audit_events) == 1

    system = systems[0]
    assert system.archived_at is None
    assert system.created_at == NOW
    assert system.viewer_groups == VIEWER_GROUPS

    audit_event = audit_events[0]
    assert audit_event.action == "system.created"
    assert audit_event.outcome == "succeeded"
    assert audit_event.object_type == "system"
    assert audit_event.object_id == str(system.system_id)


def test_create_system_replay_returns_stored_outcome_without_audit_or_insert() -> None:
    stored_payload = {
        "schema_version": "2.0.0",
        "object_type": "system",
        "system_id": str(SYSTEM_ID),
        "display_name": "Example System",
        "external_system_id": "EXT-1",
        "owner_group": OWNER_GROUP,
        "viewer_groups": VIEWER_GROUPS,
        "created_at": "2026-07-11T12:00:00Z",
        "archived_at": None,
    }
    stored_record = IdempotencyRecord(
        idempotency_record_id=uuid.uuid4(),
        principal="operator@example.test",
        operation=SYSTEMS_CREATE_OPERATION,
        idempotency_key=IDEMPOTENCY_KEY,
        request_digest=request_digest_from_payload(_digest_payload()),
        response_status=201,
        response_body=stored_payload,
        response_headers={},
        created_at=NOW,
        expires_at=NOW.replace(hour=13),
    )
    session = _RecordingSession([MagicMock(), _scalar_result(stored_record)])

    result = _run(
        create_system(
            session,
            principal=_principal(),
            audit_hmac_key=HMAC_KEY,
            idempotency_key=IDEMPOTENCY_KEY,
            display_name="Example System",
            external_system_id="EXT-1",
            owner_group=OWNER_GROUP,
            viewer_groups=VIEWER_GROUPS,
            now=NOW,
        )
    )

    assert result.replayed is True
    assert result.status == 201
    assert result.payload == stored_payload
    assert session.added == []
    assert len(session.execute_calls) == 2


def test_create_system_uses_matching_request_digest_for_replay_lookup() -> None:
    payload = _digest_payload()
    expected_digest = request_digest_from_payload(payload)
    session = _RecordingSession(
        [MagicMock(), _scalar_result(None), MagicMock(), _scalar_result(None)]
    )

    _run(
        create_system(
            session,
            principal=_principal(),
            audit_hmac_key=HMAC_KEY,
            idempotency_key=IDEMPOTENCY_KEY,
            display_name="Example System",
            external_system_id="EXT-1",
            owner_group=OWNER_GROUP,
            viewer_groups=VIEWER_GROUPS,
            now=NOW,
        )
    )

    replay_sql = _compile_sql(session.execute_calls[1])
    assert f"operation = '{SYSTEMS_CREATE_OPERATION}'" in replay_sql
    assert f"idempotency_key = '{IDEMPOTENCY_KEY}'" in replay_sql

    idempotency_rows = [obj for obj in session.added if isinstance(obj, IdempotencyRecord)]
    assert len(idempotency_rows) == 1
    assert idempotency_rows[0].request_digest == expected_digest
    assert idempotency_rows[0].response_status == 201
    assert idempotency_rows[0].response_headers == {}


def test_get_system_returns_row_for_authorized_principal() -> None:
    system = _make_system()
    session = _RecordingSession([_scalar_result(system)])

    loaded = _run(
        get_system(
            session,
            principal=_principal(groups=(OWNER_GROUP,)),
            system_id=SYSTEM_ID,
        )
    )

    assert loaded is system


def test_get_system_raises_resource_not_found_for_missing_row() -> None:
    session = _RecordingSession([_scalar_result(None)])

    with pytest.raises(ResourceNotFoundError) as exc_info:
        _run(
            get_system(
                session,
                principal=_principal(),
                system_id=SYSTEM_ID,
            )
        )

    assert exc_info.value.error_code == "resource_not_found"


def test_get_system_raises_authorization_denied_without_leakage() -> None:
    system = _make_system(
        owner_group="secret-owner-group",
        viewer_groups=["secret-viewers"],
    )
    session = _RecordingSession([_scalar_result(system)])

    with pytest.raises(AuthorizationDeniedError) as exc_info:
        _run(
            get_system(
                session,
                principal=_principal(groups=("public",)),
                system_id=SYSTEM_ID,
            )
        )

    message = str(exc_info.value)
    assert exc_info.value.error_code == "authorization_denied"
    assert "secret-owner-group" not in message
    assert "secret-viewers" not in message


def test_get_system_allows_viewer_group_membership() -> None:
    system = _make_system(owner_group="owners-only", viewer_groups=["viewers"])
    session = _RecordingSession([_scalar_result(system)])

    loaded = _run(
        get_system(
            session,
            principal=_principal(groups=("viewers",)),
            system_id=SYSTEM_ID,
        )
    )

    assert loaded is system


def test_list_systems_maps_rows_and_next_cursor() -> None:
    first = _make_system(system_id=SYSTEM_ID, created_at=NOW)
    second = _make_system(
        system_id=OTHER_SYSTEM_ID,
        created_at=NOW.replace(second=1),
    )
    session = _RecordingSession([_scalars_result([first, second])])

    page = _run(
        list_systems(
            session,
            principal=_principal(),
            cursor=None,
            limit=1,
        )
    )

    assert isinstance(page, SystemsPage)
    assert page.items == [map_system_to_domain(first)]
    assert page.next_cursor == encode_pagination_cursor(first.created_at, first.system_id)


def test_list_systems_returns_null_cursor_when_page_is_exhausted() -> None:
    system = _make_system()
    session = _RecordingSession([_scalars_result([system])])

    page = _run(
        list_systems(
            session,
            principal=_principal(),
            cursor=None,
            limit=50,
        )
    )

    assert page.next_cursor is None
    assert len(page.items) == 1
