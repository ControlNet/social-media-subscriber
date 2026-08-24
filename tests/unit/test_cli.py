from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter
from typer.testing import CliRunner

from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
    CollectionResult,
)
from social_media_subscriber.cli import create_app
from social_media_subscriber.publishing.git import (
    Published,
)
from social_media_subscriber.storage.snapshot import SnapshotSummary

if TYPE_CHECKING:
    from pathlib import Path

    from social_media_subscriber.application.collect import CollectionRequest
    from social_media_subscriber.cli_application import PublicationCommand
    from social_media_subscriber.publishing.git import PublishResult

_REPORT_ADAPTER = TypeAdapter(dict[str, str | int | list[str] | None])
_ANSI_CSI: Final = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _plain_terminal_text(value: str) -> str:
    return _ANSI_CSI.sub("", value)


class CanaryProviderError(Exception):
    pass


def _successful_collection() -> CollectionResult:
    return CollectionResult(
        CollectionExitCode.SUCCESS,
        CandidateChange.CHANGED,
        "a" * 64,
        2,
        0,
        (),
    )


@dataclass(slots=True)
class FakeApplication:
    collection_result: CollectionResult = field(default_factory=_successful_collection)
    collect_calls: list[CollectionRequest] = field(default_factory=list)
    publish_calls: list[PublicationCommand] = field(default_factory=list)
    publish_error: Exception | None = None

    def collect(self, request: CollectionRequest) -> CollectionResult:
        self.collect_calls.append(request)
        return self.collection_result

    def verify(self, snapshot: Path) -> SnapshotSummary:
        _ = snapshot
        return SnapshotSummary(
            account_count=1,
            post_count=2,
            digest="b" * 64,
        )

    def publish(self, command: PublicationCommand) -> PublishResult:
        self.publish_calls.append(command)
        if self.publish_error is not None:
            raise self.publish_error
        return Published("c" * 40)


def json_report(output: str) -> dict[str, str | int | list[str] | None]:
    lines = [line for line in output.splitlines() if line.startswith("{")]
    assert len(lines) == 1
    return _REPORT_ADAPTER.validate_json(lines[0])


_json_report = json_report


def test_help_exposes_only_approved_public_inputs() -> None:
    # Given
    runner = CliRunner()
    application = FakeApplication()

    # When
    root = runner.invoke(create_app(application), ["--help"])
    collect = runner.invoke(create_app(application), ["collect", "--help"])
    publish = runner.invoke(create_app(application), ["publish-dist", "--help"])

    # Then
    assert root.exit_code == collect.exit_code == publish.exit_code == 0
    root_help = _plain_terminal_text(root.output)
    collect_help = _plain_terminal_text(collect.output)
    publish_help = _plain_terminal_text(publish.output)
    assert {"collect", "verify-snapshot", "publish-dist"} <= set(root_help.split())
    assert "--previous-snapshot" in collect_help
    assert "--output" in collect_help
    assert "--expected-sha" in publish_help
    forbidden = ("api-key", "base-url", "platform", "payload", "fixture")
    assert all(
        token not in (collect_help + publish_help).lower() for token in forbidden
    )


def test_collect_passes_complete_explicit_date_window() -> None:
    # Given
    runner = CliRunner()
    application = FakeApplication()

    # When
    result = runner.invoke(
        create_app(application),
        [
            "collect",
            "--previous-snapshot",
            "prior",
            "--output",
            "candidate",
            "--start-date",
            "2026-08-18",
            "--end-date",
            "2026-08-20",
        ],
        env={
            "ACCOUNTS": "https://www.linkedin.com/in/synthetic/",
            "SOURCES": "brightdata:canary-secret",
        },
    )

    # Then
    assert result.exit_code == 0
    request = application.collect_calls[0]
    assert (request.start_date, request.end_date) == (
        date(2026, 8, 18),
        date(2026, 8, 20),
    )
