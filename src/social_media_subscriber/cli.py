"""Secret-safe Typer commands for collection, verification, and publication."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Never, TypedDict

import typer
from pydantic import SecretStr, ValidationError

from social_media_subscriber.application.collect import (
    CollectionRequest,
)
from social_media_subscriber.application.results import (
    CollectionExitCode,
    CollectionResult,
    aborted_result,
)
from social_media_subscriber.cli_application import (
    DIST_BRANCH,
    CliApplication,
    DefaultCliApplication,
    PublicationCommand,
)
from social_media_subscriber.cli_logging import log_exception
from social_media_subscriber.publishing.git import (
    InvalidPublicationError,
    Published,
    StalePublicationError,
    Unchanged,
)
from social_media_subscriber.publishing.process import (
    GitCommandError,
)
from social_media_subscriber.settings import Settings
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
)


class _CollectReport(TypedDict):
    candidate_change: str
    command: str
    digest: str | None
    exit_code: int
    failed_account_ids: list[str]
    failed_accounts: int
    succeeded_accounts: int


class _SnapshotReport(TypedDict):
    account_count: int
    command: str
    digest: str
    exit_code: int
    post_count: int
    source_record_count: int


class _PublishReport(TypedDict):
    command: str
    exit_code: int
    result: str
    sha: str


class _FailureReport(TypedDict):
    command: str
    error_category: str
    exit_code: int


type _MachineReport = _CollectReport | _SnapshotReport | _PublishReport | _FailureReport


def _emit(report: _MachineReport, summary: str) -> None:
    typer.echo(summary, err=True)
    typer.echo(json.dumps(report, sort_keys=True, separators=(",", ":")))


def _parse_window(
    start: str | None, end: str | None
) -> tuple[date | None, date | None]:
    if (start is None) != (end is None):
        raise CliInputError
    if start is None or end is None:
        return None, None
    try:
        parsed_start = date.fromisoformat(start)
        parsed_end = date.fromisoformat(end)
    except ValueError:
        raise CliInputError from None
    if parsed_start.isoformat() != start or parsed_end.isoformat() != end:
        raise CliInputError
    if parsed_start > parsed_end:
        raise CliInputError
    return parsed_start, parsed_end


def _collect_report(
    result: CollectionResult,
) -> _CollectReport:
    return {
        "candidate_change": result.candidate_change.value,
        "command": "collect",
        "digest": result.digest,
        "exit_code": int(result.exit_code),
        "failed_account_ids": [str(item) for item in result.failed_account_ids],
        "failed_accounts": result.failed_accounts,
        "succeeded_accounts": result.succeeded_accounts,
    }


class CliInputError(Exception):
    """Reject malformed CLI input without rendering its raw value."""


def _input_failure() -> Never:
    result = aborted_result(CollectionExitCode.INPUT)
    _emit(
        _collect_report(result), "Collection rejected: invalid input or configuration"
    )
    raise typer.Exit(int(result.exit_code))


def create_app(application: CliApplication | None = None) -> typer.Typer:
    """Create an isolated Typer application around one concrete application seam."""
    service = DefaultCliApplication() if application is None else application
    app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    _register_collect(app, service)
    _register_verification(app, service)
    _register_publication(app, service)
    return app


def _register_collect(app: typer.Typer, service: CliApplication) -> None:
    @app.command("collect")
    def collect_command(
        previous_snapshot: Annotated[Path, typer.Option("--previous-snapshot")],
        output: Annotated[Path, typer.Option("--output")],
        start_date: Annotated[str | None, typer.Option("--start-date")] = None,
        end_date: Annotated[str | None, typer.Option("--end-date")] = None,
    ) -> None:
        try:
            raw_accounts = os.environ.get("ACCOUNTS")
            raw_keys = os.environ.get("BRIGHT_DATA_API_KEYS")
            if raw_accounts is None or raw_keys is None:
                _input_failure()
            settings = Settings(
                accounts=SecretStr(raw_accounts),
                bright_data_api_keys=SecretStr(raw_keys),
            )
            if not settings.accounts.get_secret_value().strip():
                _input_failure()
            if not settings.bright_data_api_keys.get_secret_value().strip():
                _input_failure()
            start, end = _parse_window(start_date, end_date)
        except (CliInputError, ValidationError):
            _input_failure()
        request = CollectionRequest(
            settings,
            previous_snapshot,
            output,
            datetime.now(UTC),
            start,
            end,
        )
        try:
            result = service.collect(request)
        except Exception as error:  # noqa: BLE001, BROAD_EXCEPT_OK
            log_exception(error)
            result = aborted_result(CollectionExitCode.INTEGRITY)
        outcome = f"Collection complete: {result.candidate_change.value}"
        counts = (
            f"succeeded={result.succeeded_accounts}; failed={result.failed_accounts}"
        )
        _emit(_collect_report(result), f"{outcome}; {counts}")
        raise typer.Exit(int(result.exit_code))

    _ = collect_command


def _register_verification(app: typer.Typer, service: CliApplication) -> None:
    @app.command("verify-snapshot")
    def verify_snapshot_command(snapshot: Annotated[Path, typer.Argument()]) -> None:
        try:
            manifest = service.verify(snapshot)
        except SnapshotIntegrityError:
            _emit(
                {
                    "command": "verify-snapshot",
                    "error_category": "integrity",
                    "exit_code": int(CollectionExitCode.INTEGRITY),
                },
                "Snapshot verification failed: integrity",
            )
            raise typer.Exit(int(CollectionExitCode.INTEGRITY)) from None
        except Exception as error:  # noqa: BLE001, BROAD_EXCEPT_OK
            log_exception(error)
            raise typer.Exit(int(CollectionExitCode.INTEGRITY)) from None
        _emit(
            {
                "account_count": manifest.account_count,
                "command": "verify-snapshot",
                "digest": manifest.digest,
                "exit_code": 0,
                "post_count": manifest.post_count,
                "source_record_count": manifest.source_record_count,
            },
            "Snapshot verified",
        )

    _ = verify_snapshot_command


def _register_publication(app: typer.Typer, service: CliApplication) -> None:
    @app.command("publish-dist")
    def publish_dist_command(
        snapshot: Annotated[Path, typer.Option("--snapshot")],
        expected_sha: Annotated[str, typer.Option("--expected-sha")],
        remote: Annotated[str, typer.Option("--remote")] = "origin",
        branch: Annotated[str, typer.Option("--branch")] = DIST_BRANCH,
    ) -> None:
        if branch != DIST_BRANCH:
            _emit(
                {
                    "command": "publish-dist",
                    "error_category": "publication",
                    "exit_code": 6,
                },
                "Publication rejected",
            )
            raise typer.Exit(6)
        try:
            result = service.publish(
                PublicationCommand(snapshot, remote, branch, expected_sha, Path.cwd())
            )
        except (
            GitCommandError,
            InvalidPublicationError,
            SnapshotIntegrityError,
            StalePublicationError,
        ) as error:
            log_exception(error)
            _emit(
                {
                    "command": "publish-dist",
                    "error_category": "publication",
                    "exit_code": 6,
                },
                "Publication failed",
            )
            raise typer.Exit(6) from None
        except Exception as error:  # noqa: BLE001, BROAD_EXCEPT_OK
            log_exception(error)
            raise typer.Exit(6) from None
        match result:
            case Published(sha=sha):
                status = "published"
            case Unchanged(sha=sha):
                status = "unchanged"
        _emit(
            {
                "command": "publish-dist",
                "exit_code": 0,
                "result": status,
                "sha": sha,
            },
            f"Publication complete: {status}",
        )

    _ = publish_dist_command


app = create_app()
