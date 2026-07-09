"""Move failed packages to the quarantine directory."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ato_analysis.config import Settings


def quarantine_package(
    package_id: str,
    raw_path: Path,
    reason: str | dict[str, Any],
    settings: Settings,
) -> tuple[Path, Path]:
    """Write quarantined package copy and reason sidecar; return both paths."""
    settings.quarantine_dir.mkdir(parents=True, exist_ok=True)

    package_dest = settings.quarantine_dir / f"{package_id}.json"
    reason_dest = settings.quarantine_dir / f"{package_id}.reason.json"

    shutil.copy2(raw_path, package_dest)

    reason_payload: dict[str, Any] = {
        "package_id": package_id,
        "quarantined_at": datetime.now(tz=UTC).isoformat(),
        "source_path": str(raw_path.resolve()),
        "reason": reason,
    }
    reason_dest.write_text(
        json.dumps(reason_payload, indent=2, default=str),
        encoding="utf-8",
    )

    return package_dest, reason_dest
