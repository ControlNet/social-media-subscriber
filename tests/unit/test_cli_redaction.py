from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from social_media_subscriber.cli import create_app
from social_media_subscriber.publishing.git import (
    InvalidPublicationCategory,
    InvalidPublicationError,
    StalePublicationError,
)
from social_media_subscriber.publishing.process import (
    GitCommandError,
    GitInterruptedError,
)
from social_media_subscriber.storage.repository import SnapshotIntegrityError
from tests.unit.test_cli import FakeApplication

_CREDENTIAL = "synthetic-credential-canary"
_PROVIDER_TEXT = '{"status":"quota","detail":"raw-provider-canary"}'
_SNAPSHOT_ID = "synthetic_snapshot_7d91"
_INTERNAL_PATH = "/srv/subscriber/private/provider-response.json"
_EXPECTED_SHA = "e" * 40
_GENERIC_DETAIL = repr(
    (_CREDENTIAL, _PROVIDER_TEXT, _SNAPSHOT_ID, _INTERNAL_PATH),
)


def _invoke(error: Exception, *, ci: str) -> str:
    result = CliRunner().invoke(
        create_app(FakeApplication(publish_error=error)),
        [
            "publish-dist",
            "--snapshot",
            "candidate",
            "--expected-sha",
            "absent",
        ],
        env={"CI": ci},
    )
    assert result.exit_code == 6
    return result.output


def _assert_closed_failure(
    output: str,
    *,
    category: str,
    message: str,
    forbidden: tuple[str, ...],
) -> None:
    assert "cli.failure" in output
    assert category in output
    assert message in output
    assert "Traceback" not in output
    assert "stack" not in output
    for value in forbidden:
        assert value not in output


@pytest.mark.parametrize("ci", ["true", "false"], ids=["ci", "developer"])
def test_generic_exception_uses_closed_safe_failure_in_all_renderers(ci: str) -> None:
    # Given / When
    output = _invoke(RuntimeError(_GENERIC_DETAIL), ci=ci)

    # Then
    _assert_closed_failure(
        output,
        category="unhandled",
        message="Unexpected internal failure",
        forbidden=(
            "RuntimeError",
            _GENERIC_DETAIL,
            _CREDENTIAL,
            _PROVIDER_TEXT,
            _SNAPSHOT_ID,
            _INTERNAL_PATH,
        ),
    )


@pytest.mark.parametrize("ci", ["true", "false"], ids=["ci", "developer"])
@pytest.mark.parametrize(
    ("error", "category", "message", "instance_fields"),
    [
        pytest.param(
            InvalidPublicationError(InvalidPublicationCategory.EXPECTED_SHA),
            "publication_invalid",
            "Publication input or snapshot is invalid",
            (InvalidPublicationCategory.EXPECTED_SHA.value,),
            id="publication-invalid",
        ),
        pytest.param(
            StalePublicationError(_EXPECTED_SHA),
            "stale_lease",
            "Publication lease is stale",
            (_EXPECTED_SHA,),
            id="stale-lease",
        ),
        pytest.param(
            GitCommandError((_PROVIDER_TEXT, _CREDENTIAL), 73),
            "git_command",
            "Publication command failed",
            (_PROVIDER_TEXT, _CREDENTIAL, "73"),
            id="git-command",
        ),
        pytest.param(
            GitInterruptedError((_CREDENTIAL,), Path(_INTERNAL_PATH), 17.25),
            "git_interrupted",
            "Publication command interrupted",
            (_CREDENTIAL, _INTERNAL_PATH, "17.25"),
            id="git-interrupted",
        ),
        pytest.param(
            SnapshotIntegrityError(f"{_INTERNAL_PATH}: {_SNAPSHOT_ID}"),
            "integrity",
            "Snapshot integrity validation failed",
            (_INTERNAL_PATH, _SNAPSHOT_ID),
            id="snapshot-integrity",
        ),
    ],
)
def test_typed_exception_uses_only_category_and_fixed_message(
    ci: str,
    error: Exception,
    category: str,
    message: str,
    instance_fields: tuple[str, ...],
) -> None:
    # Given / When
    output = _invoke(error, ci=ci)

    # Then
    _assert_closed_failure(
        output,
        category=category,
        message=message,
        forbidden=(type(error).__name__, *instance_fields),
    )
