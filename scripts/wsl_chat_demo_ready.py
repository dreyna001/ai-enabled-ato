#!/usr/bin/env python3
"""Seed a ready FISMA revision with a succeeded run for Package Assistant chat (Bedrock demo)."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ato_service.analysis_runs import StartRunInput, start_run
from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.blobs import BlobStore
from ato_service.concurrency import format_package_revision_etag
from ato_service.db.dsn import read_database_dsn_from_file
from ato_service.db.models import AnalysisRun, PackageRevision, PackageRevisionSearchIndex
from ato_service.db.session import create_async_engine_from_url
from ato_service.deterministic_analyzer_worker import process_next_deterministic_analysis_job
from ato_service.main import AUTHORITY_MANIFEST_PATH_ENV_VAR
from ato_service.package_chat import chat_with_package, load_chat_context
from ato_service.package_revisions import (
    CreatePackageRevisionInput,
    confirm_package_revision,
    create_package_revision,
    finalize_package_revision,
)
from ato_service.package_search_index import search_revision_chunks
from ato_service.runtime_config import load_runtime_config, resolve_runtime_audit_hmac_key
from ato_service.source_artifacts import upload_source_artifact
from ato_service.synthetic_intake import process_next_synthetic_extraction, process_next_synthetic_scan
from ato_service.systems import create_system

DEFAULT_INSTALL_DIR = Path("/opt/ato-analyzer")
DEFAULT_DSN_FILE = Path("/etc/ato-analyzer/credentials/database-dsn")
OWNER = AuthenticatedPrincipal(
    actor_id="dev-portal-user",
    groups=("owners",),
    csrf_token="c" * 32,
    allowed_origins=("http://localhost:5173",),
)


def _demo_package_path(repo_root: Path) -> Path:
    return repo_root / "data/synthetic-packages/fisma-demo-portal/agency-security-plan-excerpt.json"


def _load_hmac_key(config_path: Path, install_dir: Path) -> bytes:
    config = load_runtime_config(config_path, base_dir=install_dir)
    key = resolve_runtime_audit_hmac_key(config)
    if len(key) < MIN_AUDIT_HMAC_KEY_BYTES:
        raise RuntimeError("audit HMAC key is too short")
    return key


async def _seed_demo(
    *,
    install_dir: Path,
    config_path: Path,
    dsn_file: Path,
    repo_root: Path,
    display_name: str,
    dry_run: bool,
) -> dict[str, str]:
    demo_package = _demo_package_path(repo_root)
    if not demo_package.is_file():
        raise FileNotFoundError(f"Missing demo package: {demo_package}")

    config = load_runtime_config(config_path, base_dir=install_dir)
    hmac_key = _load_hmac_key(config_path, install_dir)
    dsn = read_database_dsn_from_file(dsn_file)
    engine = create_async_engine_from_url(dsn)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    blob_store = BlobStore(config.storage_data_path)
    now = datetime.now(timezone.utc)
    authority_manifest_id = "ato-authorities-2026-07-10-draft"
    profile_id = "fisma_agency_security"
    idem_suffix = now.strftime("%Y%m%d%H%M%S")

    async with session_factory() as session:
        system_result = await create_system(
            session,
            principal=OWNER,
            audit_hmac_key=hmac_key,
            idempotency_key=f"chat-demo-system-{idem_suffix}",
            now=now,
            display_name=display_name,
            external_system_id=None,
            owner_group="owners",
            viewer_groups=["viewers"],
            customer_enterprise_id=config.installation_customer_enterprise_id,
        )
        system_id = uuid.UUID(system_result.payload["system_id"])

        revision_result = await create_package_revision(
            session,
            principal=OWNER,
            system_id=system_id,
            request=CreatePackageRevisionInput(
                parent_revision_id=None,
                profile_id=profile_id,
                certification_class=None,
                impact_level="moderate",
                data_origin="synthetic",
                sensitivity="internal_unclassified",
            ),
            authority_manifest_id=authority_manifest_id,
            idempotency_key=f"chat-demo-revision-{idem_suffix}",
            hmac_key=hmac_key,
            now=now,
        )
        revision_id = uuid.UUID(revision_result.payload["package_revision_id"])

        await upload_source_artifact(
            session,
            principal=OWNER,
            audit_hmac_key=hmac_key,
            blob_store=blob_store,
            limits=config.limits,
            package_revision_id=revision_id,
            idempotency_key=f"chat-demo-upload-{idem_suffix}",
            source=io.BytesIO(demo_package.read_bytes()),
            display_filename="agency-security-plan-excerpt.json",
            declared_media_type="application/json",
            artifact_kind="manifest",
            source_date=None,
            now=now,
        )

        await finalize_package_revision(
            session,
            principal=OWNER,
            package_revision_id=revision_id,
            idempotency_key=f"chat-demo-finalize-{idem_suffix}",
            hmac_key=hmac_key,
            storage_root=config.storage_data_path,
            project_root=repo_root,
            limits=config.limits,
            now=now,
        )

        if await process_next_synthetic_scan(session, hmac_key=hmac_key, now=now) is None:
            raise RuntimeError("synthetic scan did not advance the revision")
        if await process_next_synthetic_extraction(
            session,
            blob_store=blob_store,
            hmac_key=hmac_key,
            now=now,
        ) is None:
            raise RuntimeError("synthetic extraction did not build a package draft")

        revision = await session.get(PackageRevision, revision_id)
        if revision is None or revision.status != "awaiting_confirmation":
            raise RuntimeError(
                f"expected awaiting_confirmation, got {revision.status if revision else 'missing'}"
            )

        etag = format_package_revision_etag(revision.revision_version)
        await confirm_package_revision(
            session,
            principal=OWNER,
            package_revision_id=revision_id,
            if_match=etag,
            idempotency_key=f"chat-demo-confirm-{idem_suffix}",
            hmac_key=hmac_key,
            now=now,
            project_root=repo_root,
            config=config,
            blob_store=blob_store,
        )

        search_count = await session.scalar(
            select(func.count(PackageRevisionSearchIndex.package_revision_id)).where(
                PackageRevisionSearchIndex.package_revision_id == revision_id
            )
        )
        if int(search_count or 0) < 1:
            raise RuntimeError("search index was not built during confirm")

        search_page = await search_revision_chunks(
            session,
            package_revision_id=revision_id,
            query="access control",
            limit=5,
        )
        if not search_page.items:
            raise RuntimeError("search index returned no hits for 'access control'")

        started = await start_run(
            session,
            principal=OWNER,
            package_revision_id=revision_id,
            request=StartRunInput(
                run_type="deterministic_only",
                parent_run_id=None,
                assessment_item_ids=(),
            ),
            config=config,
            authority_manifest_id=authority_manifest_id,
            project_root=repo_root,
            idempotency_key=f"chat-demo-run-{idem_suffix}",
            hmac_key=hmac_key,
            now=now,
        )
        run_id = uuid.UUID(started.payload["run_id"])

        if await process_next_deterministic_analysis_job(
            session,
            storage_root=config.storage_data_path,
            project_root=repo_root,
            hmac_key=hmac_key,
            lease_owner="wsl-chat-demo-seed",
            now=now,
            config=config,
        ) is None:
            raise RuntimeError("deterministic analysis job did not complete")

        run = await session.get(AnalysisRun, run_id)
        if run is None or run.status != "succeeded":
            raise RuntimeError(f"expected succeeded run, got {run.status if run else 'missing'}")

        context = await load_chat_context(
            session,
            package_revision_id=revision_id,
            run_id=run_id,
            review_revision_id=None,
        )
        chat_result = await chat_with_package(
            session,
            config=config,
            blob_store=blob_store,
            principal=OWNER,
            context=context,
            question="What is documented for AC-1 access control?",
            limits=config.chat_limits,
            now=now,
        )

        if chat_result.get("refused"):
            raise RuntimeError(
                f"chat refused: {chat_result.get('refusal_code') or 'unknown'}"
            )
        answer = str(chat_result.get("answer") or "").strip()
        if not answer:
            raise RuntimeError("chat returned an empty answer")

        if dry_run:
            await session.rollback()
        else:
            await session.commit()

        return {
            "system_id": str(system_id).lower(),
            "package_revision_id": str(revision_id).lower(),
            "run_id": str(run_id).lower(),
            "display_name": display_name,
            "chat_answer_preview": answer[:240],
            "search_hits": str(len(search_page.items)),
            "portal_url": (
                f"http://localhost:5173/workflow/systems/{system_id}/revisions/{revision_id}"
            ),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, default=DEFAULT_INSTALL_DIR)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_INSTALL_DIR / "runtime-config.json",
    )
    parser.add_argument("--dsn-file", type=Path, default=DEFAULT_DSN_FILE)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root for authority/profile assets during confirm and analysis",
    )
    parser.add_argument(
        "--display-name",
        default="Chat Demo (Bedrock)",
        help="System display name shown in the portal",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.config.is_file():
        print(f"ERROR: missing runtime config: {args.config}", file=sys.stderr)
        return 1
    if not args.dsn_file.is_file():
        print(f"ERROR: missing database DSN file: {args.dsn_file}", file=sys.stderr)
        return 1

    manifest_path = args.repo_root / "docs/contracts/authority-manifest.json"
    if not manifest_path.is_file():
        print(f"ERROR: missing authority manifest: {manifest_path}", file=sys.stderr)
        return 1

    os.environ.setdefault(AUTHORITY_MANIFEST_PATH_ENV_VAR, str(manifest_path))

    try:
        result = asyncio.run(
            _seed_demo(
                install_dir=args.install_dir,
                config_path=args.config,
                dsn_file=args.dsn_file,
                repo_root=args.repo_root,
                display_name=args.display_name,
                dry_run=args.dry_run,
            )
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    print()
    print("Chat demo is ready.")
    print(f"Open: {result['portal_url']}")
    print("Select the succeeded deterministic run, then use Package Assistant → Ask.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
