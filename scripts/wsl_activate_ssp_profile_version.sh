#!/usr/bin/env bash
# Activate one imported SSP profile version for local WSL (dev operator tool).
set -euo pipefail

readonly INSTALL_DIR="/opt/ato-analyzer"
readonly DATABASE_DSN_CREDENTIAL_PATH="/etc/ato-analyzer/credentials/database-dsn"
PROFILE_VERSION="${1:-1.2.0}"

usage() {
    cat <<EOF
Usage: $(basename "$0") [profile-version]

Makes the given imported profile version active for its profile_key (deactivates
the prior active row). Default version: 1.2.0

Example:
  sudo bash scripts/wsl_activate_ssp_profile_version.sh 1.2.0
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: run with sudo so database-dsn can be read" >&2
    exit 1
fi

[[ -f "$DATABASE_DSN_CREDENTIAL_PATH" ]] || {
    echo "ERROR: missing $DATABASE_DSN_CREDENTIAL_PATH" >&2
    exit 1
}
[[ -x "$INSTALL_DIR/venv/bin/python" ]] || {
    echo "ERROR: missing $INSTALL_DIR/venv/bin/python (run upgrade.sh first)" >&2
    exit 1
}

export ATO_DATABASE_DSN_FILE="$DATABASE_DSN_CREDENTIAL_PATH"
export ACTIVATE_SSP_PROFILE_VERSION="$PROFILE_VERSION"
export PYTHONPATH="$INSTALL_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

"$INSTALL_DIR/venv/bin/python" - <<'PY'
import asyncio
import os
from datetime import datetime, timezone

from sqlalchemy import select

from ato_service.db.models import SspProfileVersion
from ato_service.db.session import create_async_engine_from_url, create_session_factory, session_scope
from ato_service.ssp_workspace.profiles import ProfileNotFoundError, activate_profile


async def main() -> None:
    dsn_path = os.environ["ATO_DATABASE_DSN_FILE"]
    dsn = open(dsn_path, encoding="utf-8").read().strip()
    version = os.environ["ACTIVATE_SSP_PROFILE_VERSION"]
    factory = create_session_factory(create_async_engine_from_url(dsn))
    async with session_scope(factory) as session:
        row = (
            await session.execute(
                select(SspProfileVersion).where(SspProfileVersion.version == version)
            )
        ).scalar_one_or_none()
        if row is None:
            raise SystemExit(f"No imported profile version {version!r}")
        activated = await activate_profile(
            session,
            profile_version_id=row.profile_version_id,
            now=datetime.now(timezone.utc),
        )
        print(
            f"Active profile: {activated.profile_key} version {activated.version} "
            f"(profile_version_id={activated.profile_version_id})"
        )


try:
    asyncio.run(main())
except ProfileNotFoundError as exc:
    raise SystemExit(str(exc)) from exc
PY
