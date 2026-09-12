"""FastAPI routes for the bounded internal SSP workflow."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.api_dependencies import (
    get_audit_hmac_key,
    get_blob_store,
    get_db_session,
    get_runtime_state,
)
from ato_service.auth_context import (
    AuthenticatedPrincipal,
    AuthorizationDeniedError,
    require_authenticated_principal,
    require_mutation_context,
    require_system_read_access,
)
from ato_service.blobs import BlobStore, BlobStoreError
from ato_service.malware_scan import MalwareScannerUnavailableError
from ato_service.package_rbac import principal_has_role, require_any_package_role
from ato_service.problems import (
    FieldError,
    build_problem,
    get_request_id,
    problem_json_response,
    sanitize_detail,
)
from ato_service.ssp_workspace.categorization import CategorizationValidationError
from ato_service.ssp_workspace.chat import load_chat_history, send_chat_message
from ato_service.ssp_workspace.chat_contracts import ChatHistory, ChatMessageRequest
from ato_service.ssp_workspace.contracts import EvidenceLink, ProfileState
from ato_service.ssp_workspace.editing import WorkspaceEditError
from ato_service.ssp_workspace.evidence import (
    EvidenceRemovalError,
    EvidenceUploadError,
    ingest_workspace_evidence,
    remove_workspace_evidence,
)
from ato_service.ssp_workspace.generation import SspGenerationError
from ato_service.ssp_workspace.model_policy import (
    SspModelPolicyError,
    require_ssp_model_allowed,
)
from ato_service.ssp_workspace.model_runtime import SspContextBudgetError
from ato_service.ssp_workspace.persistence import WorkspacePersistenceError
from ato_service.ssp_workspace.profile_bundles import ProfileBundleError
from ato_service.ssp_workspace.profiles import (
    ProfilePersistenceError,
    activate_profile,
    import_profile,
    list_profiles,
    parse_profile_archive,
)
from ato_service.ssp_workspace.service import (
    AgencyDocxRenderNotFoundError,
    AgencyDocxRenderStateError,
    AgencyDocxUploadError,
    AgentPatchNotFoundError,
    AgentPatchStateError,
    ApprovalNotFoundError,
    WorkspaceNotReviewableError,
    agency_docx_output_filename,
    apply_proposed_patch,
    approve_agency_docx_render,
    approve_workspace_revision,
    analyze_workspace_categorization,
    analyze_workspace_diagram,
    create_agency_docx_render,
    create_initialized_workspace,
    generate_workspace_draft,
    list_workspace_rows,
    load_workspace_envelope,
    migrate_workspace_profile,
    propose_agent_patch,
    read_agency_docx_download_bytes,
    read_agency_docx_preview_bytes,
    reject_agency_docx_render,
    reject_proposed_patch,
    render_approved_export,
    restore_workspace_revision,
    save_control_edit,
    save_information_types,
    save_question_answer,
    save_section_edit,
    save_system_categorization,
    save_system_definition,
)
from ato_service.systems import create_system, list_systems
from ato_service.text_llm import TextModelCallError, TextModelConfigurationError


_SAFE_CONTEXT_BUDGET_DETAIL = (
    "SSP request exceeds the configured aggregate context budget; reduce the "
    "selected evidence or use an approved larger-context profile"
)


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_id: uuid.UUID
    profile_version_id: uuid.UUID


class CreateSspSystemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=255)


IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{16,128}$",
    ),
]


class ExpectedRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision_id: uuid.UUID


class EditSectionRequest(ExpectedRevisionRequest):
    content: str = Field(max_length=100_000)


class EditControlRequest(ExpectedRevisionRequest):
    implementation_statement: str = Field(max_length=100_000)
    implementation_status: str | None = Field(default=None, max_length=64)
    responsibility: str | None = Field(default=None, max_length=64)


class AnswerQuestionRequest(ExpectedRevisionRequest):
    answer: str = Field(min_length=1, max_length=20_000)


class CategorizationEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: uuid.UUID
    locator: dict[str, Any] = Field(min_length=1)


class SaveCategorizationRequest(ExpectedRevisionRequest):
    confidentiality: str = Field(pattern=r"^(low|moderate|high)$")
    integrity: str = Field(pattern=r"^(low|moderate|high)$")
    availability: str = Field(pattern=r"^(low|moderate|high)$")
    confidentiality_rationale: str = Field(min_length=1, max_length=4_000)
    integrity_rationale: str = Field(min_length=1, max_length=4_000)
    availability_rationale: str = Field(min_length=1, max_length=4_000)
    confidentiality_evidence: tuple[CategorizationEvidenceRequest, ...] = Field(
        min_length=1
    )
    integrity_evidence: tuple[CategorizationEvidenceRequest, ...] = Field(min_length=1)
    availability_evidence: tuple[CategorizationEvidenceRequest, ...] = Field(
        min_length=1
    )


class EvidenceLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: uuid.UUID
    locator: dict[str, Any] = Field(min_length=1)


class DiagramLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: uuid.UUID
    locator: dict[str, Any] = Field(min_length=1)
    label: str = Field(default="", max_length=255)


class SystemComponentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(default="", max_length=128)
    name: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=4_000)
    placement: Literal["inside", "outside", "crossing"]
    evidence: tuple[EvidenceLinkRequest, ...] = ()


class InterconnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interconnection_id: str = Field(default="", max_length=128)
    connected_organization: str = Field(min_length=1, max_length=255)
    connected_system: str = Field(min_length=1, max_length=255)
    direction: Literal["inbound", "outbound", "bidirectional"]
    data_types: tuple[str, ...] = Field(min_length=1)
    interface_protocol: str = Field(min_length=1, max_length=255)
    connection_owner: str = Field(min_length=1, max_length=255)
    agreement_type: str = Field(min_length=1, max_length=255)
    agreement_id: str = Field(default="", max_length=255)
    agreement_status: str = Field(default="", max_length=255)
    agreement_expiration: str = Field(default="", max_length=64)
    boundary_protections: str = Field(min_length=1, max_length=4_000)
    evidence: tuple[EvidenceLinkRequest, ...] = ()


class SaveSystemDefinitionRequest(ExpectedRevisionRequest):
    boundary_narrative: str = Field(min_length=20, max_length=100_000)
    diagram_links: tuple[DiagramLinkRequest, ...] = ()
    components: tuple[SystemComponentRequest, ...] = Field(min_length=1)
    interconnections: tuple[InterconnectionRequest, ...] = Field(min_length=1)


class AnalyzeDiagramRequest(ExpectedRevisionRequest):
    artifact_id: uuid.UUID
    page_number: int = Field(default=1, ge=1, le=500)


class InformationTypeMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(default="", max_length=128)
    catalog_identifier: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=4_000)
    adjusted_confidentiality: Literal["low", "moderate", "high"] | None = None
    adjusted_integrity: Literal["low", "moderate", "high"] | None = None
    adjusted_availability: Literal["low", "moderate", "high"] | None = None
    adjustment_rationale: str = Field(default="", max_length=4_000)
    evidence: tuple[EvidenceLinkRequest, ...] = ()


class SaveInformationTypesRequest(ExpectedRevisionRequest):
    information_types: tuple[InformationTypeMappingRequest, ...] = Field(min_length=1)


class ProposePatchRequest(ExpectedRevisionRequest):
    instruction: str = Field(min_length=1, max_length=20_000)


class MigrateProfileRequest(ExpectedRevisionRequest):
    profile_version_id: uuid.UUID
    impact_level: str = Field(pattern=r"^(low|moderate|high)$")


class SspProfileVersionResponse(BaseModel):
    """Profile version metadata exposed to the administration portal."""

    model_config = ConfigDict(extra="forbid")

    profile_version_id: uuid.UUID
    profile_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    status: ProfileState
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    imported_by: str = Field(min_length=1, max_length=255)
    imported_at: datetime
    activated_at: datetime | None
    display_name: str = Field(min_length=1)


class SspProfilesResponse(BaseModel):
    """Profile list plus the server-authoritative administration capability."""

    model_config = ConfigDict(extra="forbid")

    can_manage: bool
    items: list[SspProfileVersionResponse]


ExportFormat = Literal["json", "docx", "oscal-json"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def get_read_principal(request: Request) -> AuthenticatedPrincipal:
    return require_authenticated_principal(request)


def get_mutation_principal(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    origin: Annotated[str | None, Header()] = None,
) -> AuthenticatedPrincipal:
    return require_mutation_context(request, x_csrf_token, origin)


def _error_response(exc: Exception) -> JSONResponse:
    code = getattr(exc, "error_code", "validation_failed")
    if isinstance(exc, AuthorizationDeniedError):
        return JSONResponse(
            status_code=403,
            content={"error": "authorization_denied", "error_code": "authorization_denied"},
        )
    if isinstance(exc, SspContextBudgetError) or getattr(exc, "failure_kind", None) == "context_budget":
        status = 422
        code = "model_context_budget_exceeded"
    elif code in {"resource_not_found", "approval_not_found"}:
        status = 404
    elif code in {
        "revision_stale",
        "chat_sequence_conflict",
        "chat_request_conflict",
        "chat_context_stale",
        "illegal_state_transition",
        "profile_already_imported",
    }:
        status = 409
    elif code in {
        "model_policy_not_approved",
        "model_routing_denied",
        "prohibited_model_action",
    }:
        status = 403
    elif code in {"malware_scan_unavailable", "storage_unavailable"}:
        status = 503
    elif code in {"chat_rate_limit_exceeded", "chat_limit_exceeded"}:
        status = 429
    elif code in {"chat_model_failed", "chat_model_output_invalid"}:
        status = 502
    elif code == "chat_retention_unsupported":
        status = 503
    elif code in {"agency_docx_upload_failed", "malware_scan_required"}:
        status = 422
    elif isinstance(exc, (TextModelConfigurationError,)):
        status = 503
        code = "model_not_configured"
    elif isinstance(exc, (TextModelCallError, SspGenerationError)):
        status = 502
        code = "model_generation_failed"
    else:
        status = 422
    content: dict[str, str] = {"error": code, "error_code": code}
    if isinstance(exc, SspGenerationError) and exc.failure_kind == "source_binding":
        message = (
            "Document generation produced content that is not linked to uploaded "
            "evidence. Upload and process intake artifacts, then try Generate again. "
            f"({sanitize_detail(exc.detail)})"
        )
    elif isinstance(exc, SspContextBudgetError) or getattr(exc, "failure_kind", None) == "context_budget":
        message = _SAFE_CONTEXT_BUDGET_DETAIL
    elif isinstance(exc, SspModelPolicyError):
        message = str(exc)
    elif isinstance(exc, (WorkspaceEditError, CategorizationValidationError)):
        message = sanitize_detail(str(exc))
    elif code.startswith("chat_"):
        message = {
            "chat_sequence_conflict": "The conversation changed or another message is in progress. Refresh history before retrying.",
            "chat_request_conflict": "This request identifier was already used. Refresh history before sending a new message.",
            "chat_context_stale": "System evidence changed while the answer was being prepared. Refresh and ask again.",
            "chat_input_limit": "The message exceeds the configured chat input limit. Shorten the message and try again.",
            "chat_rate_limit_exceeded": "The chat request limit was reached. Wait before trying again.",
            "chat_limit_exceeded": "The daily chat budget was reached.",
            "chat_model_failed": "The assistant could not complete this request. Saved history is unchanged.",
            "chat_model_output_invalid": "The assistant response failed source or output validation and was not saved.",
            "chat_retention_unsupported": "The configured retention policy requires an approved chatbot policy update.",
        }.get(code, "")
    elif code in {
        "agency_docx_upload_failed",
        "malware_scan_required",
        "malware_scan_unavailable",
        "evidence_upload_failed",
        "validation_failed",
    }:
        message = ""
    else:
        message = ""
    if message and message not in {code, "validation_failed"}:
        content["detail"] = message
    return JSONResponse(status_code=status, content=content)


def _categorization_validation_response(
    request: Request,
    exc: CategorizationValidationError,
) -> JSONResponse:
    problem = build_problem(
        error_code=exc.error_code,
        status=422,
        instance=request.url.path,
        request_id=get_request_id(request),
        detail=str(exc),
        field_errors=[
            FieldError(
                path=exc.field,
                code="invalid_value",
                message=str(exc),
            )
        ],
    )
    return problem_json_response(problem)


async def _authorize_workspace(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    workspace_id: uuid.UUID,
    roles: tuple[str, ...],
) -> Any:
    from ato_service.db.models import SspWorkspace, System

    row = (
        await session.execute(
            select(SspWorkspace, System)
            .join(System, System.system_id == SspWorkspace.system_id)
            .where(SspWorkspace.workspace_id == workspace_id)
        )
    ).one_or_none()
    if row is None:
        from ato_service.ssp_workspace.persistence import WorkspaceNotFoundError

        raise WorkspaceNotFoundError("workspace not found")
    _, system = row
    # A platform role never grants access to another system's context or edits.
    require_system_read_access(principal, system)
    require_any_package_role(principal, system=system, roles=roles)
    return system


async def _finish_read_only_boundary(session: AsyncSession) -> None:
    """End authorization's read transaction before a slow SSP workflow."""
    if isinstance(session, AsyncSession) and session.in_transaction():
        if session.new or session.dirty or session.deleted:
            raise ValueError("authorization left pending database mutations")
        await session.rollback()


def _require_platform_admin(principal: AuthenticatedPrincipal) -> None:
    if not principal_has_role(principal, "platform_admin"):
        raise AuthorizationDeniedError()


def _model_adapter(runtime_state: Any) -> Any:
    config = runtime_state.config
    require_ssp_model_allowed(config)
    adapter = getattr(runtime_state, "ssp_model_adapter", None)
    if adapter is None:
        raise TextModelConfigurationError(
            "SSP model adapter is not available in application runtime"
        )
    return adapter


def build_ssp_workspace_router() -> APIRouter:
    router = APIRouter(tags=["SSP Workspaces"])

    @router.get("/ssp-workspaces/{workspace_id}/chat", response_model=ChatHistory)
    async def get_chat(
        workspace_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        before_sequence: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 40,
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("viewer",)
            )
            history = await load_chat_history(
                session,
                workspace_id=workspace_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                before_sequence=before_sequence,
                limit=limit,
            )
            return JSONResponse(
                content=history.model_dump(mode="json"),
                headers={"Cache-Control": "no-store"},
            )
        except (AuthorizationDeniedError, WorkspacePersistenceError) as exc:
            await session.rollback()
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/chat", response_model=ChatHistory)
    async def post_chat(
        workspace_id: uuid.UUID,
        payload: ChatMessageRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("viewer",)
            )
            await _finish_read_only_boundary(session)
            history = await send_chat_message(
                session,
                workspace_id=workspace_id,
                actor_id=principal.actor_id,
                payload=payload,
                model=_model_adapter(runtime_state),
                config=runtime_state.config,
                now=_utc_now(),
            )
            # Recheck current system access before committing or exposing the answer.
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("viewer",)
            )
            await session.commit()
            return JSONResponse(
                content=history.model_dump(mode="json"),
                headers={"Cache-Control": "no-store"},
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            TextModelConfigurationError,
            TextModelCallError,
            SspGenerationError,
            SspModelPolicyError,
        ) as exc:
            # Caught errors must not let the request dependency commit pending turns.
            await session.rollback()
            return _error_response(exc)

    @router.get("/ssp-systems")
    async def get_ssp_systems(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, Any]:
        page = await list_systems(
            session,
            principal=principal,
            cursor=None,
            limit=100,
            include_archived=False,
        )
        return {"items": page.items}

    @router.post("/ssp-systems", status_code=201)
    async def post_ssp_system(
        body: CreateSspSystemRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        idempotency_key: IdempotencyKeyHeader,
    ) -> JSONResponse:
        owner_group = principal.groups[0]
        result = await create_system(
            session,
            principal=principal,
            audit_hmac_key=audit_hmac_key,
            idempotency_key=idempotency_key,
            display_name=body.display_name,
            external_system_id=None,
            owner_group=owner_group,
            viewer_groups=[],
            customer_enterprise_id=(
                runtime_state.config.installation_customer_enterprise_id
            ),
            now=_utc_now(),
        )
        return JSONResponse(status_code=result.status, content=result.payload)

    @router.get("/ssp-profiles", response_model=SspProfilesResponse)
    async def get_profiles(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> SspProfilesResponse:
        rows = await list_profiles(session)
        return SspProfilesResponse(
            can_manage=principal_has_role(principal, "platform_admin"),
            items=[
                SspProfileVersionResponse(
                    profile_version_id=item.profile_version_id,
                    profile_id=item.profile_key,
                    version=item.version,
                    status=item.status,
                    bundle_sha256=item.bundle_sha256,
                    imported_by=item.imported_by,
                    imported_at=item.imported_at,
                    activated_at=item.activated_at,
                    display_name=item.bundle["manifest"]["display_name"],
                )
                for item in rows
            ],
        )

    @router.post("/ssp-profiles/import")
    async def post_profile_import(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        bundle: Annotated[UploadFile, File()],
    ) -> Response:
        try:
            _require_platform_admin(principal)
            content = await bundle.read(52_428_801)
            row = await import_profile(
                session,
                bundle=parse_profile_archive(content),
                imported_by=principal.actor_id,
                now=_utc_now(),
            )
            return JSONResponse(
                status_code=201,
                content={"profile_version_id": str(row.profile_version_id), "status": row.status},
            )
        except (
            AuthorizationDeniedError,
            ProfileBundleError,
            ProfilePersistenceError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-profiles/{profile_version_id}/activate")
    async def post_profile_activate(
        profile_version_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> Response:
        try:
            _require_platform_admin(principal)
            row = await activate_profile(
                session,
                profile_version_id=profile_version_id,
                now=_utc_now(),
            )
            return JSONResponse(
                status_code=200,
                content={"profile_version_id": str(row.profile_version_id), "status": row.status},
            )
        except (AuthorizationDeniedError, ProfilePersistenceError) as exc:
            return _error_response(exc)

    @router.get("/ssp-workspaces/sp800-60-catalog")
    async def get_sp800_60_catalog(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
    ) -> Response:
        from ato_service.ssp_workspace.sp800_60_catalog import (
            Sp80060CatalogError,
            catalog_document_for_api,
        )

        _ = principal
        try:
            content = catalog_document_for_api()
        except Sp80060CatalogError as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "catalog_unavailable",
                    "error_code": "catalog_unavailable",
                    "detail": str(exc),
                },
            )
        return JSONResponse(status_code=200, content=content)

    @router.get("/ssp-workspaces")
    async def get_workspaces(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for row in await list_workspace_rows(session):
            try:
                await _authorize_workspace(
                    session,
                    principal=principal,
                    workspace_id=row.workspace_id,
                    roles=("viewer",),
                )
            except AuthorizationDeniedError:
                continue
            items.append(await load_workspace_envelope(session, workspace_id=row.workspace_id))
        return {"items": items}

    @router.post("/ssp-workspaces")
    async def post_workspace(
        payload: CreateWorkspaceRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        from ato_service.db.models import System

        try:
            system = (
                await session.execute(
                    select(System).where(System.system_id == payload.system_id)
                )
            ).scalar_one_or_none()
            if system is None:
                from ato_service.ssp_workspace.persistence import WorkspaceNotFoundError

                raise WorkspaceNotFoundError("system not found")
            require_any_package_role(
                principal,
                system=system,
                roles=("system_owner", "isso"),
            )
            workspace = await create_initialized_workspace(
                session,
                system_id=payload.system_id,
                profile_version_id=payload.profile_version_id,
                impact_level=None,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            envelope = await load_workspace_envelope(
                session, workspace_id=workspace.workspace_id
            )
            return JSONResponse(status_code=201, content=envelope)
        except (AuthorizationDeniedError, WorkspacePersistenceError, ProfileBundleError) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/system-definition")
    async def post_system_definition(
        workspace_id: uuid.UUID,
        payload: SaveSystemDefinitionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session,
                principal=principal,
                workspace_id=workspace_id,
                roles=("isso",),
            )
            await save_system_definition(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                boundary_narrative=payload.boundary_narrative,
                diagram_links=tuple(
                    link.model_dump(mode="json") for link in payload.diagram_links
                ),
                components=tuple(
                    component.model_dump(mode="json") for component in payload.components
                ),
                interconnections=tuple(
                    item.model_dump(mode="json") for item in payload.interconnections
                ),
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(
                    session, workspace_id=workspace_id
                ),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            ProfileBundleError,
            ValueError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/diagram-analysis")
    async def post_diagram_analysis(
        workspace_id: uuid.UUID,
        payload: AnalyzeDiagramRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        blob_store: Annotated[BlobStore, Depends(get_blob_store)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session,
                principal=principal,
                workspace_id=workspace_id,
                roles=("isso",),
            )
            await _finish_read_only_boundary(session)
            await analyze_workspace_diagram(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                artifact_id=payload.artifact_id,
                page_number=payload.page_number,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
                blob_store=blob_store,
                config=runtime_state.config,
                vision_client=getattr(runtime_state, "ssp_vision_adapter", None),
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(
                    session, workspace_id=workspace_id
                ),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            ProfileBundleError,
            SspModelPolicyError,
            ValueError,
        ) as exc:
            message = str(exc)
            if "vision model is not configured" in message:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "model_not_configured",
                        "error_code": "model_not_configured",
                    },
                )
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/information-types")
    async def post_information_types(
        workspace_id: uuid.UUID,
        payload: SaveInformationTypesRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session,
                principal=principal,
                workspace_id=workspace_id,
                roles=("isso",),
            )
            await save_information_types(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                mappings=tuple(
                    item.model_dump(mode="json") for item in payload.information_types
                ),
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(
                    session, workspace_id=workspace_id
                ),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            ProfileBundleError,
            ValueError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/categorization/analyze")
    async def post_categorization_analyze(
        workspace_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session,
                principal=principal,
                workspace_id=workspace_id,
                roles=("isso",),
            )
            await _finish_read_only_boundary(session)
            model = _model_adapter(runtime_state)
            await analyze_workspace_categorization(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                model=model,
                config=runtime_state.config,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(
                    session, workspace_id=workspace_id
                ),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            WorkspaceEditError,
            TextModelConfigurationError,
            TextModelCallError,
            SspGenerationError,
            SspModelPolicyError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/categorization")
    async def post_categorization(
        request: Request,
        workspace_id: uuid.UUID,
        payload: SaveCategorizationRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session,
                principal=principal,
                workspace_id=workspace_id,
                roles=("isso",),
            )
            await save_system_categorization(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                confidentiality=payload.confidentiality,
                integrity=payload.integrity,
                availability=payload.availability,
                confidentiality_rationale=payload.confidentiality_rationale,
                integrity_rationale=payload.integrity_rationale,
                availability_rationale=payload.availability_rationale,
                confidentiality_evidence=tuple(
                    EvidenceLink(
                        artifact_id=item.artifact_id,
                        locator=item.locator,
                    )
                    for item in payload.confidentiality_evidence
                ),
                integrity_evidence=tuple(
                    EvidenceLink(
                        artifact_id=item.artifact_id,
                        locator=item.locator,
                    )
                    for item in payload.integrity_evidence
                ),
                availability_evidence=tuple(
                    EvidenceLink(
                        artifact_id=item.artifact_id,
                        locator=item.locator,
                    )
                    for item in payload.availability_evidence
                ),
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(
                    session, workspace_id=workspace_id
                ),
            )
        except CategorizationValidationError as exc:
            return _categorization_validation_response(request, exc)
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            ValueError,
        ) as exc:
            return _error_response(exc)

    @router.get("/ssp-workspaces/{workspace_id}")
    async def get_workspace(
        workspace_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("viewer",)
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            WorkspaceNotReviewableError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/evidence")
    async def post_evidence(
        workspace_id: uuid.UUID,
        expected_revision_id: Annotated[uuid.UUID, Form()],
        file: Annotated[UploadFile, File()],
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        blob_store: Annotated[BlobStore, Depends(get_blob_store)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session,
                principal=principal,
                workspace_id=workspace_id,
                roles=("system_owner", "isso"),
            )
            await _finish_read_only_boundary(session)
            content = await file.read(runtime_state.config.limits.max_single_file_bytes + 1)
            await ingest_workspace_evidence(
                session,
                workspace_id=workspace_id,
                expected_revision_id=expected_revision_id,
                filename=file.filename or "evidence",
                media_type=file.content_type or "application/octet-stream",
                content=content,
                actor_id=principal.actor_id,
                now=_utc_now(),
                blob_store=blob_store,
                config=runtime_state.config,
                vision_client=getattr(runtime_state, "ssp_vision_adapter", None),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=201,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            EvidenceUploadError,
            BlobStoreError,
            SspModelPolicyError,
            MalwareScannerUnavailableError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/generate")
    async def post_generate(
        workspace_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await _finish_read_only_boundary(session)
            model = _model_adapter(runtime_state)
            await generate_workspace_draft(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                model=model,
                config=runtime_state.config,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            TextModelConfigurationError,
            TextModelCallError,
            SspGenerationError,
            SspModelPolicyError,
        ) as exc:
            return _error_response(exc)

    @router.delete(
        "/ssp-workspaces/{workspace_id}/evidence/{evidence_artifact_id}"
    )
    async def delete_evidence(
        workspace_id: uuid.UUID,
        evidence_artifact_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[
            AuthenticatedPrincipal, Depends(get_mutation_principal)
        ],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session,
                principal=principal,
                workspace_id=workspace_id,
                roles=("system_owner", "isso"),
            )
            await remove_workspace_evidence(
                session,
                workspace_id=workspace_id,
                evidence_artifact_id=evidence_artifact_id,
                expected_revision_id=payload.expected_revision_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(
                    session, workspace_id=workspace_id
                ),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            EvidenceRemovalError,
        ) as exc:
            return _error_response(exc)

    @router.patch("/ssp-workspaces/{workspace_id}/sections/{section_key}")
    async def patch_section(
        workspace_id: uuid.UUID,
        section_key: str,
        payload: EditSectionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        return await _edit_route(
            session=session,
            principal=principal,
            workspace_id=workspace_id,
            audit_hmac_key=audit_hmac_key,
            operation=lambda: save_section_edit(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                section_key=section_key,
                content=payload.content,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            ),
        )

    @router.patch("/ssp-workspaces/{workspace_id}/controls/{control_id}")
    async def patch_control(
        workspace_id: uuid.UUID,
        control_id: str,
        payload: EditControlRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        return await _edit_route(
            session=session,
            principal=principal,
            workspace_id=workspace_id,
            audit_hmac_key=audit_hmac_key,
            operation=lambda: save_control_edit(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                control_id=control_id,
                implementation_statement=payload.implementation_statement,
                implementation_status=payload.implementation_status,
                responsibility=payload.responsibility,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            ),
        )

    @router.post("/ssp-workspaces/{workspace_id}/questions/{question_id}/answer")
    async def post_question_answer(
        workspace_id: uuid.UUID,
        question_id: uuid.UUID,
        payload: AnswerQuestionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        return await _edit_route(
            session=session,
            principal=principal,
            workspace_id=workspace_id,
            audit_hmac_key=audit_hmac_key,
            operation=lambda: save_question_answer(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                question_id=question_id,
                answer=payload.answer,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            ),
        )

    @router.post("/ssp-workspaces/{workspace_id}/agent/patches")
    async def post_agent_patch(
        workspace_id: uuid.UUID,
        payload: ProposePatchRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await _finish_read_only_boundary(session)
            model = _model_adapter(runtime_state)
            patch = await propose_agent_patch(
                session,
                workspace_id=workspace_id,
                expected_revision_id=payload.expected_revision_id,
                instruction=payload.instruction,
                model=model,
                config=runtime_state.config,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=201,
                content={
                    "patch_id": str(patch.patch_id),
                    "status": patch.status,
                    "summary": patch.summary,
                    "operations": patch.operations,
                },
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            TextModelConfigurationError,
            TextModelCallError,
            SspGenerationError,
            SspModelPolicyError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/agent/patches/{patch_id}/apply")
    async def post_apply_patch(
        workspace_id: uuid.UUID,
        patch_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await apply_proposed_patch(
                session,
                workspace_id=workspace_id,
                patch_id=patch_id,
                expected_revision_id=payload.expected_revision_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            AgentPatchNotFoundError,
            AgentPatchStateError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/agent/patches/{patch_id}/reject")
    async def post_reject_patch(
        workspace_id: uuid.UUID,
        patch_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await reject_proposed_patch(
                session,
                workspace_id=workspace_id,
                patch_id=patch_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            AgentPatchNotFoundError,
            AgentPatchStateError,
        ) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/approve")
    async def post_approve(
        workspace_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await approve_workspace_revision(
                session,
                workspace_id=workspace_id,
                revision_id=payload.expected_revision_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            WorkspacePersistenceError,
            WorkspaceNotReviewableError,
        ) as exc:
            return _error_response(exc)

    @router.post(
        "/ssp-workspaces/{workspace_id}/revisions/{revision_id}/restore"
    )
    async def post_restore_revision(
        workspace_id: uuid.UUID,
        revision_id: uuid.UUID,
        payload: ExpectedRevisionRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await restore_workspace_revision(
                session,
                workspace_id=workspace_id,
                revision_id=revision_id,
                expected_revision_id=payload.expected_revision_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (AuthorizationDeniedError, WorkspacePersistenceError, ValueError) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/migrate-profile")
    async def post_migrate_profile(
        workspace_id: uuid.UUID,
        payload: MigrateProfileRequest,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            _, diff = await migrate_workspace_profile(
                session,
                workspace_id=workspace_id,
                profile_version_id=payload.profile_version_id,
                impact_level=payload.impact_level,
                expected_revision_id=payload.expected_revision_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            envelope = await load_workspace_envelope(session, workspace_id=workspace_id)
            envelope["profile_migration_diff"] = asdict(diff)
            return JSONResponse(status_code=200, content=envelope)
        except (AuthorizationDeniedError, WorkspacePersistenceError, ValueError) as exc:
            return _error_response(exc)

    @router.get("/ssp-workspaces/{workspace_id}/exports/{export_format}")
    async def get_export(
        workspace_id: uuid.UUID,
        export_format: ExportFormat,
        revision_id: uuid.UUID,
        include_open_questions: bool,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("viewer",)
            )
            content = await render_approved_export(
                session,
                workspace_id=workspace_id,
                revision_id=revision_id,
                export_format=export_format,
                include_open_questions=include_open_questions,
            )
            if export_format == "docx":
                media_type = (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
                filename = f"ssp-{revision_id}.docx"
            elif export_format == "oscal-json":
                media_type = "application/json"
                filename = f"ssp-{revision_id}.oscal.json"
            else:
                media_type = "application/json"
                filename = f"ssp-{revision_id}.json"
            return Response(
                content=content,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "private, no-store",
                },
            )
        except (AuthorizationDeniedError, ApprovalNotFoundError, ValueError) as exc:
            return _error_response(exc)

    @router.post("/ssp-workspaces/{workspace_id}/agency-docx-renders")
    async def post_agency_docx_render(
        workspace_id: uuid.UUID,
        revision_id: Annotated[uuid.UUID, Form()],
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
        blob_store: Annotated[BlobStore, Depends(get_blob_store)],
        runtime_state: Annotated[Any, Depends(get_runtime_state)],
        file: UploadFile = File(...),
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await _finish_read_only_boundary(session)
            payload = await file.read(runtime_state.config.limits.max_single_file_bytes + 1)
            model = _model_adapter(runtime_state)
            await create_agency_docx_render(
                session,
                workspace_id=workspace_id,
                source_revision_id=revision_id,
                template_filename=file.filename or "template.docx",
                template_bytes=payload,
                actor_id=principal.actor_id,
                now=_utc_now(),
                blob_store=blob_store,
                config=runtime_state.config,
                model=model,
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            AgencyDocxUploadError,
            ApprovalNotFoundError,
            BlobStoreError,
            MalwareScannerUnavailableError,
            TextModelConfigurationError,
            TextModelCallError,
            SspGenerationError,
            SspModelPolicyError,
        ) as exc:
            return _error_response(exc)

    @router.get(
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/preview"
    )
    async def get_agency_docx_preview(
        workspace_id: uuid.UUID,
        render_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        blob_store: Annotated[BlobStore, Depends(get_blob_store)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("viewer",)
            )
            content = await read_agency_docx_preview_bytes(
                session,
                workspace_id=workspace_id,
                render_id=render_id,
                blob_store=blob_store,
            )
            return Response(
                content=content,
                media_type=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{agency_docx_output_filename(render_id)}"'
                    ),
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "private, no-store",
                },
            )
        except (
            AuthorizationDeniedError,
            AgencyDocxRenderNotFoundError,
            AgencyDocxRenderStateError,
        ) as exc:
            return _error_response(exc)

    @router.get(
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/download"
    )
    async def get_agency_docx_download(
        workspace_id: uuid.UUID,
        render_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_read_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        blob_store: Annotated[BlobStore, Depends(get_blob_store)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("viewer",)
            )
            content = await read_agency_docx_download_bytes(
                session,
                workspace_id=workspace_id,
                render_id=render_id,
                blob_store=blob_store,
            )
            return Response(
                content=content,
                media_type=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{agency_docx_output_filename(render_id)}"'
                    ),
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "private, no-store",
                },
            )
        except (
            AuthorizationDeniedError,
            AgencyDocxRenderNotFoundError,
            AgencyDocxRenderStateError,
        ) as exc:
            return _error_response(exc)

    @router.post(
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/approve"
    )
    async def post_agency_docx_approve(
        workspace_id: uuid.UUID,
        render_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await approve_agency_docx_render(
                session,
                workspace_id=workspace_id,
                render_id=render_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            AgencyDocxRenderNotFoundError,
            AgencyDocxRenderStateError,
        ) as exc:
            return _error_response(exc)

    @router.post(
        "/ssp-workspaces/{workspace_id}/agency-docx-renders/{render_id}/reject"
    )
    async def post_agency_docx_reject(
        workspace_id: uuid.UUID,
        render_id: uuid.UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(get_mutation_principal)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
        audit_hmac_key: Annotated[bytes, Depends(get_audit_hmac_key)],
    ) -> Response:
        try:
            await _authorize_workspace(
                session, principal=principal, workspace_id=workspace_id, roles=("isso",)
            )
            await reject_agency_docx_render(
                session,
                workspace_id=workspace_id,
                render_id=render_id,
                actor_id=principal.actor_id,
                now=_utc_now(),
                audit_hmac_key=audit_hmac_key,
            )
            return JSONResponse(
                status_code=200,
                content=await load_workspace_envelope(session, workspace_id=workspace_id),
            )
        except (
            AuthorizationDeniedError,
            AgencyDocxRenderNotFoundError,
            AgencyDocxRenderStateError,
        ) as exc:
            return _error_response(exc)

    return router


async def _edit_route(
    *,
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    workspace_id: uuid.UUID,
    audit_hmac_key: bytes,
    operation: Any,
) -> Response:
    del audit_hmac_key
    try:
        await _authorize_workspace(
            session, principal=principal, workspace_id=workspace_id, roles=("isso",)
        )
        await operation()
        return JSONResponse(
            status_code=200,
            content=await load_workspace_envelope(session, workspace_id=workspace_id),
        )
    except (AuthorizationDeniedError, WorkspacePersistenceError, ValueError) as exc:
        return _error_response(exc)
