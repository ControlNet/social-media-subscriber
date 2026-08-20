from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import pytest
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
from social_media_subscriber.storage.snapshot import SnapshotManifest

if TYPE_CHECKING:
    from pathlib import Path

    from social_media_subscriber.application.collect import CollectionRequest
    from social_media_subscriber.cli_application import PublicationCommand
    from social_media_subscriber.publishing.git import PublishResult

_REPORT_ADAPTER = TypeAdapter(dict[str, str | int | list[str] | None])


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

    def verify(self, snapshot: Path) -> SnapshotManifest:
        _ = snapshot
        return SnapshotManifest(
            account_count=1,
            post_count=2,
            source_record_count=2,
            digest="b" * 64,
        )

    def publish(self, command: PublicationCommand) -> PublishResult:
        self.publish_calls.append(command)
        if self.publish_error is not None:
            raise self.publish_error
        return Published("c" * 40)


def _json_report(output: str) -> dict[str, str | int | list[str] | None]:
    lines = [line for line in output.splitlines() if line.startswith("{")]
    assert len(lines) == 1
    return _REPORT_ADAPTER.validate_json(lines[0])


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
    assert {"collect", "verify-snapshot", "publish-dist"} <= set(root.output.split())
    assert "--previous-snapshot" in collect.output
    assert "--output" in collect.output
    assert "--expected-sha" in publish.output
    forbidden = ("api-key", "base-url", "platform", "payload", "fixture")
    assert all(
        token not in (collect.output + publish.output).lower() for token in forbidden
    )


def test_collect_reads_secrets_from_environment_and_emits_one_json_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.delenv("ACCOUNTS", raising=False)
    monkeypatch.delenv("BRIGHT_DATA_API_KEYS", raising=False)
    runner = CliRunner()
    application = FakeApplication()
    environment = {
        "ACCOUNTS": "https://www.linkedin.com/in/synthetic/",
        "BRIGHT_DATA_API_KEYS": "canary-secret",
    }

    # When
    result = runner.invoke(
        create_app(application),
        ["collect", "--previous-snapshot", "prior", "--output", "candidate"],
        env=environment,
    )

    # Then
    assert result.exit_code == 0
    assert len(application.collect_calls) == 1
    report = _json_report(result.output)
    assert report == {
        "candidate_change": "changed",
        "command": "collect",
        "digest": "a" * 64,
        "exit_code": 0,
        "failed_account_ids": [],
        "failed_accounts": 0,
        "succeeded_accounts": 2,
    }
    assert "canary-secret" not in result.output


def test_collect_rejects_missing_or_blank_secrets_before_application_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.delenv("ACCOUNTS", raising=False)
    monkeypatch.delenv("BRIGHT_DATA_API_KEYS", raising=False)
    runner = CliRunner()
    application = FakeApplication()

    # When
    missing = runner.invoke(
        create_app(application),
        ["collect", "--previous-snapshot", "prior", "--output", "candidate"],
        env={},
    )
    blank = runner.invoke(
        create_app(application),
        ["collect", "--previous-snapshot", "prior", "--output", "candidate"],
        env={"ACCOUNTS": " ", "BRIGHT_DATA_API_KEYS": "\n"},
    )

    # Then
    assert missing.exit_code == blank.exit_code == 2
    assert application.collect_calls == []
    assert _json_report(missing.output)["exit_code"] == 2
    assert _json_report(blank.output)["exit_code"] == 2


def test_collect_rejects_malformed_one_sided_and_inverted_dates() -> None:
    # Given
    runner = CliRunner()
    application = FakeApplication()
    environment = {
        "ACCOUNTS": "https://www.linkedin.com/in/synthetic/",
        "BRIGHT_DATA_API_KEYS": "canary-secret",
    }
    base = ["collect", "--previous-snapshot", "prior", "--output", "candidate"]

    # When
    malformed = runner.invoke(
        create_app(application), [*base, "--start-date", "yesterday"], env=environment
    )
    one_sided = runner.invoke(
        create_app(application), [*base, "--start-date", "2026-08-19"], env=environment
    )
    inverted = runner.invoke(
        create_app(application),
        [
            *base,
            "--start-date",
            "2026-08-20",
            "--end-date",
            "2026-08-19",
        ],
        env=environment,
    )

    # Then
    assert [malformed.exit_code, one_sided.exit_code, inverted.exit_code] == [2, 2, 2]
    assert application.collect_calls == []
    assert all(
        _json_report(item.output)["exit_code"] == 2
        for item in (malformed, one_sided, inverted)
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
            "BRIGHT_DATA_API_KEYS": "canary-secret",
        },
    )

    # Then
    assert result.exit_code == 0
    request = application.collect_calls[0]
    assert (request.start_date, request.end_date) == (
        date(2026, 8, 18),
        date(2026, 8, 20),
    )


def test_publish_rejects_non_dist_branch_without_application_call() -> None:
    # Given
    runner = CliRunner()
    application = FakeApplication()

    # When
    result = runner.invoke(
        create_app(application),
        [
            "publish-dist",
            "--snapshot",
            "candidate",
            "--remote",
            "origin",
            "--branch",
            "main",
            "--expected-sha",
            "absent",
        ],
    )

    # Then
    assert result.exit_code == 6
    assert application.publish_calls == []
    assert _json_report(result.output)["exit_code"] == 6


@pytest.mark.parametrize(
    ("exit_code", "candidate_change"),
    [
        (CollectionExitCode.PROVIDER, CandidateChange.ABSENT),
        (CollectionExitCode.PARTIAL, CandidateChange.CHANGED),
        (CollectionExitCode.INTEGRITY, CandidateChange.ABSENT),
    ],
)
def test_collect_preserves_application_exit_contract(
    exit_code: CollectionExitCode,
    candidate_change: CandidateChange,
) -> None:
    # Given
    application = FakeApplication(
        collection_result=CollectionResult(
            exit_code,
            candidate_change,
            "d" * 64 if candidate_change is CandidateChange.CHANGED else None,
            1 if exit_code is CollectionExitCode.PARTIAL else 0,
            1 if exit_code is CollectionExitCode.PARTIAL else 0,
            (),
        )
    )

    # When
    result = CliRunner().invoke(
        create_app(application),
        ["collect", "--previous-snapshot", "prior", "--output", "candidate"],
        env={
            "ACCOUNTS": "https://www.linkedin.com/in/synthetic/",
            "BRIGHT_DATA_API_KEYS": "canary-secret",
        },
    )

    # Then
    assert result.exit_code == int(exit_code)
    assert _json_report(result.output)["candidate_change"] == candidate_change.value


def test_ci_exception_log_preserves_context_and_redacts_secret_url() -> None:
    # Given
    application = FakeApplication(
        publish_error=CanaryProviderError(
            "provider canary-secret failed at https://canary.invalid/private"
        )
    )

    # When
    result = CliRunner().invoke(
        create_app(application),
        [
            "publish-dist",
            "--snapshot",
            "candidate",
            "--expected-sha",
            "absent",
        ],
        env={
            "CI": "true",
            "ACCOUNTS": "https://canary.invalid/private",
            "BRIGHT_DATA_API_KEYS": "canary-secret",
        },
    )

    # Then
    assert result.exit_code == 6
    log = next(
        line for line in result.output.splitlines() if '"event":"cli.failure"' in line
    )
    assert '"error_type":"CanaryProviderError"' in log
    assert '"category":"unhandled"' in log
    assert '"stack":"' in log
    assert "[REDACTED]" in log
    assert "canary-secret" not in result.output
    assert "https://canary.invalid" not in result.output
