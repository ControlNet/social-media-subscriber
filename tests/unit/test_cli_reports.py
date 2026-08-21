from __future__ import annotations

import pytest
from typer.testing import CliRunner

from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
    CollectionResult,
)
from social_media_subscriber.cli import create_app
from tests.unit.test_cli import FakeApplication, json_report


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
    assert json_report(result.output) == {
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
    assert json_report(missing.output)["exit_code"] == 2
    assert json_report(blank.output)["exit_code"] == 2


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
        [*base, "--start-date", "2026-08-20", "--end-date", "2026-08-19"],
        env=environment,
    )

    # Then
    assert [malformed.exit_code, one_sided.exit_code, inverted.exit_code] == [2, 2, 2]
    assert application.collect_calls == []
    assert all(
        json_report(item.output)["exit_code"] == 2
        for item in (malformed, one_sided, inverted)
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
    assert json_report(result.output)["exit_code"] == 6


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
    assert json_report(result.output)["candidate_change"] == candidate_change.value
