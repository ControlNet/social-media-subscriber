from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import anyio
from anyio.lowlevel import checkpoint
from pydantic import TypeAdapter
from typer.testing import CliRunner

from social_media_subscriber.application.collect import (
    CollectionRequest,
    collect_snapshot,
)
from social_media_subscriber.cli import create_app
from social_media_subscriber.cli_application import (
    DefaultCliApplication,
    PublicationCommand,
)
from social_media_subscriber.providers.brightdata.client import BrightDataClient
from social_media_subscriber.providers.http import HttpClientConfig
from tests.e2e.brightdata_server import (
    ACTIVE_VALUE,
    COMPANY_URL,
    PERSON_URL,
    REVOKED_VALUE,
    FakeBrightDataServer,
)

if TYPE_CHECKING:
    from pathlib import Path

    from typer import Typer

    from social_media_subscriber.application.results import CollectionResult
    from social_media_subscriber.publishing.git import PublishResult
    from social_media_subscriber.storage.snapshot import SnapshotManifest
    from tests.e2e.git_harness import CliResult

_RUNNER: Final = CliRunner()
_REPORT: Final = TypeAdapter(dict[str, str | int | list[str] | None])


async def _no_wait(_delay: float) -> None:
    await checkpoint()


@dataclass(frozen=True, slots=True)
class E2eApplication:
    base_url: str
    delegate: DefaultCliApplication = field(default_factory=DefaultCliApplication)

    def collect(self, request: CollectionRequest) -> CollectionResult:
        def build_client(credential: str) -> BrightDataClient:
            return BrightDataClient(
                credential,
                HttpClientConfig(self.base_url),
                sleeper=_no_wait,
            )

        return anyio.run(collect_snapshot, request, build_client)

    def verify(self, snapshot: Path) -> SnapshotManifest:
        return self.delegate.verify(snapshot)

    def publish(self, command: PublicationCommand) -> PublishResult:
        return self.delegate.publish(command)


def publication_app() -> Typer:
    return create_app(E2eApplication("http://127.0.0.1:1"))


def invoke_collect(
    server: FakeBrightDataServer,
    previous: Path,
    candidate: Path,
    *,
    accounts: str = f"{PERSON_URL}\n{COMPANY_URL}",
) -> CliResult:
    return _RUNNER.invoke(
        create_app(E2eApplication(server.base_url)),
        [
            "collect",
            "--previous-snapshot",
            str(previous),
            "--output",
            str(candidate),
            "--start-date",
            "2026-08-17",
            "--end-date",
            "2026-08-20",
        ],
        env={
            "ACCOUNTS": accounts,
            "BRIGHT_DATA_API_KEYS": f"{REVOKED_VALUE}\n{ACTIVE_VALUE}",
        },
        catch_exceptions=False,
    )


def report(result: CliResult) -> dict[str, str | int | list[str] | None]:
    reports = [line for line in result.output.splitlines() if line.startswith("{")]
    assert len(reports) == 1
    return _REPORT.validate_json(reports[0])


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
