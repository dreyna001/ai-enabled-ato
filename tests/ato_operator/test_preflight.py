"""Unit tests for capability-aware operator preflight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ato_operator.preflight import _collect_required_credentials
from ato_service.process_capabilities import resolve_process_capabilities
from ato_service.runtime_config import load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]
ONPREM_EXAMPLE = ROOT / "deployment" / "config" / "runtime-config.onprem.example.json"


@pytest.fixture
def onprem_document(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(Path, "is_absolute", lambda self: True)
    return json.loads(ONPREM_EXAMPLE.read_text(encoding="utf-8"))


def test_inactive_capabilities_reduce_required_credentials(onprem_document: dict) -> None:
    onprem_document["PROCESS_CAPABILITIES"] = {
        **onprem_document["PROCESS_CAPABILITIES"],
        "oidc_authentication": False,
        "text_model_calls": False,
        "malware_scanning": False,
    }
    config = load_runtime_config_from_dict(onprem_document, base_dir=None)
    capabilities = resolve_process_capabilities(config.document)
    assert capabilities is not None
    required = _collect_required_credentials(config, capabilities)
    keys = {item[0] for item in required}
    assert "OIDC_CLIENT_CREDENTIAL_REFERENCE" not in keys
    assert "TEXT_MODEL_CREDENTIAL_REFERENCE" not in keys
