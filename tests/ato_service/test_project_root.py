"""Tests for install-root resolution used by contract schema loaders."""

from __future__ import annotations

from pathlib import Path

from ato_service.project_root import contract_path, find_project_root


def test_find_project_root_from_installed_package_layout(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "ato-analyzer"
    package_dir = (
        install_root
        / "venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "ato_service"
    )
    schema_dir = install_root / "docs" / "contracts"
    package_dir.mkdir(parents=True)
    schema_dir.mkdir(parents=True)
    (install_root / "pyproject.toml").write_text("[project]\nname='ato'\n", encoding="utf-8")
    (schema_dir / "package-draft-document.schema.json").write_text("{}", encoding="utf-8")

    module_path = package_dir / "project_root.py"
    module_path.write_text("", encoding="utf-8")

    root = find_project_root(module_path)
    assert root == install_root.resolve()
    assert contract_path(
        "package-draft-document.schema.json",
        start=module_path,
    ).is_file()
