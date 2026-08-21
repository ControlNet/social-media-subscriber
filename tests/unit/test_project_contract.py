from __future__ import annotations

import re
from dataclasses import fields
from importlib.util import find_spec
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter
from typer.testing import CliRunner

from social_media_subscriber import adapters
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
    CollectionResult,
)
from social_media_subscriber.cli import create_app
from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.providers import brightdata
from social_media_subscriber.providers.brightdata.models import JsonValue
from tests.unit.test_cli import FakeApplication, json_report
from tests.workflow_helpers import load_workflow, mapping, sequence, text

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
BOUNDARY_SCHEMAS: Final = {
    "account.schema.json": {
        "schema_version",
        "id",
        "platform",
        "kind",
        "profile_url",
        "first_seen_at",
    },
    "post.schema.json": {
        "schema_version",
        "id",
        "platform_post_id",
        "account_id",
        "canonical_url",
        "published_at",
        "text",
        "kind",
        "hashtags",
        "links",
        "first_seen_at",
        "content_hash",
    },
    "brightdata-linkedin-post.schema.json": {
        "schema_version",
        "provider",
        "dataset_id",
        "platform_post_id",
        "account_id",
        "payload_sha256",
        "payload",
    },
}
FORBIDDEN_BOUNDARY_FIELDS: Final = frozenset(
    {
        "api_key",
        "credential",
        "platform_account_id",
        "profile_id",
        "url_aliases",
        "user_id",
    }
)
_JSON_OBJECT: Final = TypeAdapter(dict[str, JsonValue])
_COLLECT_WORKFLOW: Final = PROJECT_ROOT / ".github" / "workflows" / "collect.yml"


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


def test_only_posts_collection_is_a_public_routing_operation() -> None:
    # Given / When
    operations = tuple(AdapterOperation)

    # Then
    assert operations == (AdapterOperation.COLLECT_ACCOUNT_POSTS,)
    assert AdapterOperation.COLLECT_ACCOUNT_POSTS.value == "collect_account_posts"


def test_workflow_invokes_the_single_posts_route_before_publication() -> None:
    # Given / When
    workflow = load_workflow(_COLLECT_WORKFLOW)
    publication = mapping(mapping(workflow["jobs"])["publication"])
    steps = [mapping(step) for step in sequence(publication["steps"])]
    collect = next(step for step in steps if step.get("name") == "Collect candidate")
    publish = next(
        step for step in steps if step.get("name") == "Verify and publish candidate"
    )
    collect_source = text(collect["run"])
    publish_source = text(publish["run"])

    # Then
    assert steps.index(collect) < steps.index(publish)
    assert re.search(r"collect_arguments=\(\s*collect\s", collect_source)
    assert collect_source.count("pixi run subscriber") == 1
    assert not {
        "collect-identities",
        "collect-profiles",
        "--operation",
        "--platform-account-id",
        "--url-alias",
        "--user-id",
    }.intersection(collect_source.split())
    assert (
        'if [[ "$COLLECT_STATUS" -ne 0 && "$COLLECT_STATUS" -ne 4 ]]' in collect_source
    )
    assert "verify-snapshot" not in collect_source
    assert "publish-dist" not in collect_source
    assert publish_source.index("verify-snapshot") < publish_source.index(
        "publish-dist"
    )


def test_collection_exit_and_report_contract_remains_stable_and_private() -> None:
    # Given
    failed_url = "https://www.linkedin.com/in/synthetic-workflow-contract/"
    credential_canary = "synthetic-workflow-credential"
    application = FakeApplication(
        collection_result=CollectionResult(
            CollectionExitCode.PARTIAL,
            CandidateChange.UNCHANGED,
            "d" * 64,
            0,
            1,
            (AccountId(failed_url),),
        )
    )

    # When
    result = CliRunner().invoke(
        create_app(application),
        ["collect", "--previous-snapshot", "prior", "--output", "candidate"],
        env={
            "ACCOUNTS": failed_url,
            "BRIGHT_DATA_API_KEYS": credential_canary,
        },
    )

    # Then
    assert tuple(CollectionExitCode) == (
        CollectionExitCode.SUCCESS,
        CollectionExitCode.INPUT,
        CollectionExitCode.PROVIDER,
        CollectionExitCode.PARTIAL,
        CollectionExitCode.INTEGRITY,
    )
    assert tuple(int(item) for item in CollectionExitCode) == (0, 2, 3, 4, 5)
    assert tuple(field.name for field in fields(CollectionResult)) == (
        "exit_code",
        "candidate_change",
        "digest",
        "succeeded_accounts",
        "failed_accounts",
        "failed_account_ids",
    )
    assert result.exit_code == 4
    assert json_report(result.output) == {
        "candidate_change": "unchanged",
        "command": "collect",
        "digest": "d" * 64,
        "exit_code": 4,
        "failed_account_ids": [failed_url],
        "failed_accounts": 1,
        "succeeded_accounts": 0,
    }
    assert credential_canary not in result.output


def test_boundary_schemas_are_closed_v2_url_owned_and_privacy_safe() -> None:
    # Given / When
    schemas = {
        name: _JSON_OBJECT.validate_json((PROJECT_ROOT / "schemas" / name).read_bytes())
        for name in BOUNDARY_SCHEMAS
    }

    # Then
    for name, expected_properties in BOUNDARY_SCHEMAS.items():
        schema = schemas[name]
        properties = schema["properties"]
        assert isinstance(properties, dict)
        schema_version = properties["schema_version"]
        assert isinstance(schema_version, dict)
        assert schema_version["const"] == 2
        assert schema["additionalProperties"] is False
        assert set(properties) == expected_properties
        assert FORBIDDEN_BOUNDARY_FIELDS.isdisjoint(properties)

    account_properties = schemas["account.schema.json"]["properties"]
    post_properties = schemas["post.schema.json"]["properties"]
    source_properties = schemas["brightdata-linkedin-post.schema.json"]["properties"]
    assert isinstance(account_properties, dict)
    assert isinstance(post_properties, dict)
    assert isinstance(source_properties, dict)
    account_id = account_properties["id"]
    profile_url = account_properties["profile_url"]
    post_owner = post_properties["account_id"]
    source_owner = source_properties["account_id"]
    assert isinstance(account_id, dict)
    assert isinstance(profile_url, dict)
    assert isinstance(post_owner, dict)
    assert isinstance(source_owner, dict)
    assert account_id["pattern"] == profile_url["pattern"]
    assert account_id["pattern"] == post_owner["pattern"]
    assert account_id["pattern"] == source_owner["pattern"]
