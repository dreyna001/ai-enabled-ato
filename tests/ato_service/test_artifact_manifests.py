"""Artifact manifest writer tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ato_service.artifact_manifests import (
    GeneratedRunFile,
    write_artifact_manifest,
    write_run_output_file,
)

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "33333333-3333-4333-8333-333333333333"
REVISION_ID = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def test_artifact_manifest_orders_files_deterministically(tmp_path: Path) -> None:
    storage_root = tmp_path
    beta = write_run_output_file(
        storage_root=storage_root,
        run_id=RUN_ID,
        relative_path="machine/matrix.json",
        payload=b'{"rows":[]}',
    )
    alpha = write_run_output_file(
        storage_root=storage_root,
        run_id=RUN_ID,
        relative_path="human/summary.md",
        payload=b"# summary",
    )

    manifest = write_artifact_manifest(
        run_id=RUN_ID,
        package_revision_id=REVISION_ID,
        authority_manifest_id="authority.v2",
        analysis_profile_sha256="a" * 64,
        config_fingerprint="b" * 64,
        prompt_bundle_sha256="c" * 64,
        completed_at=NOW,
        generated_files=[beta, alpha],
        storage_root=storage_root,
        project_root=ROOT,
    )

    paths = [item["path"] for item in manifest.document["files"]]
    assert paths == ["human/summary.md", "machine/matrix.json"]
    assert manifest.document["files"][0]["sha256"] == alpha.sha256

    manifest_bytes = json.dumps(
        manifest.document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest_path = storage_root / "runs" / RUN_ID / "artifact-manifest.json"
    assert manifest_path.read_bytes() == manifest_bytes
