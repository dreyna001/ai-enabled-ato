"""Portal and intake-worker deployment contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PORTAL_PACKAGE = ROOT / "portal" / "package.json"
PORTAL_VITE_CONFIG = ROOT / "portal" / "vite.config.ts"
PORTAL_APP = ROOT / "portal" / "src" / "App.tsx"
PORTAL_NGINX = ROOT / "deployment" / "nginx" / "ato-portal.conf"
INTAKE_UNIT = ROOT / "deployment" / "systemd" / "ato-intake-worker.service"
PORTAL_RUNTIME_EXAMPLE = (
    ROOT / "deployment" / "config" / "runtime-config.dev_local.portal.example.json"
)


@pytest.mark.parametrize(
    "path",
    [
        PORTAL_PACKAGE,
        PORTAL_VITE_CONFIG,
        PORTAL_APP,
        PORTAL_NGINX,
        INTAKE_UNIT,
        PORTAL_RUNTIME_EXAMPLE,
    ],
)
def test_portal_assets_exist(path: Path) -> None:
    assert path.is_file(), f"missing portal asset: {path}"


def test_portal_package_declares_build_script() -> None:
    text = PORTAL_PACKAGE.read_text(encoding="utf-8")
    assert '"build": "tsc -b && vite build"' in text


def test_portal_nginx_proxies_api_and_serves_spa() -> None:
    text = PORTAL_NGINX.read_text(encoding="utf-8")
    assert "location /api/" in text
    assert "try_files $uri $uri/ /index.html;" in text
    assert "/opt/ato-analyzer/portal/dist" in text
    assert "Content-Security-Policy" in text


def test_intake_worker_systemd_unit_runs_long_lived_process() -> None:
    text = INTAKE_UNIT.read_text(encoding="utf-8")
    assert "ato-intake-worker" in text
    assert "Type=simple" in text
    assert "Restart=on-failure" in text
    assert "ato-synthetic-intake-worker" not in text


def test_portal_runtime_example_declares_oidc_and_portal_origin() -> None:
    text = PORTAL_RUNTIME_EXAMPLE.read_text(encoding="utf-8")
    assert "IDENTITY_PROVIDER_MODE" in text
    assert "PORTAL_PUBLIC_ORIGIN" in text
    assert "OIDC_ISSUER_URL" in text
