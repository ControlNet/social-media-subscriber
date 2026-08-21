from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Final

from social_media_subscriber import adapters
from social_media_subscriber.providers import brightdata

PROJECT_ROOT: Final = Path(__file__).parents[2]
REQUIRED_TASKS: Final = frozenset(
    {
        "actionlint",
        "format",
        "format-check",
        "lint",
        "schemas",
        "schemas-check",
        "subscriber",
        "test",
        "typecheck",
        "verify",
    }
)


def test_package_is_importable_from_the_src_layout() -> None:
    # Given / When
    package_spec = find_spec("social_media_subscriber")

    # Then
    assert package_spec is not None
    assert package_spec.parent == "social_media_subscriber"


def test_python_and_strict_tool_settings_are_machine_configured() -> None:
    # Given / When
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    # Then
    assert 'requires-python = ">=3.13,<3.14"' in pyproject
    assert 'typeCheckingMode = "all"' in pyproject
    assert 'select = ["ALL"]' in pyproject
    assert '"--strict-config"' in pyproject
    assert '"--strict-markers"' in pyproject


def test_pixi_environment_and_required_tasks_are_declared() -> None:
    # Given / When
    pixi_manifest = (PROJECT_ROOT / "pixi.toml").read_text(encoding="utf-8")

    # Then
    assert 'python = "3.13.*"' in pixi_manifest
    for task in REQUIRED_TASKS:
        assert f"{task} =" in pixi_manifest

    verify_task = next(
        line for line in pixi_manifest.splitlines() if line.startswith("verify =")
    )
    assert '"schemas-check"' in verify_task


def test_basedpyright_resolves_the_pixi_default_environment() -> None:
    # Given / When
    pyright_config = (PROJECT_ROOT / "pyrightconfig.json").read_text(encoding="utf-8")

    # Then
    assert '"venvPath": ".pixi/envs"' in pyright_config
    assert '"venv": "default"' in pyright_config


def test_public_adapter_packages_expose_only_posts_contracts() -> None:
    # Given / When
    adapter_exports = frozenset(adapters.__all__)
    brightdata_exports = frozenset(brightdata.__all__)

    # Then
    assert adapter_exports == {
        "AdapterDriver",
        "AdapterMetadata",
        "AdapterOperation",
        "AdapterRegistry",
        "AdapterResolution",
        "DuplicateAdapterDescriptorError",
        "DuplicateAdapterDriverError",
        "InvalidAdapterMetadataError",
        "MetadataViolation",
        "MissingAdapterMetadataError",
        "ResolvedAdapterDrivers",
        "UnsupportedAdapterCapability",
        "adapter",
    }
    assert brightdata_exports == {
        "BrightDataLinkedInPostSourceRecord",
        "BrightDataPost",
        "normalize_posts",
    }
