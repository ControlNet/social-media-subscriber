"""Production adapters behind the command-line application seam."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import anyio

from social_media_subscriber.application.collect import (
    CollectionRequest,
    collect_snapshot,
)
from social_media_subscriber.publishing.git import (
    InvalidPublicationCategory,
    InvalidPublicationError,
    PublishRequest,
    PublishResult,
    StalePublicationError,
    publish_snapshot,
)
from social_media_subscriber.publishing.process import (
    GitCommandError,
    GitRunner,
    run_git,
)
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityCategory,
    SnapshotIntegrityError,
    SnapshotRepository,
)

if TYPE_CHECKING:
    from social_media_subscriber.application.results import CollectionResult
    from social_media_subscriber.storage.snapshot import SnapshotManifest

DIST_BRANCH = "dist"
_DIST_REF = "refs/heads/dist"
_ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class PublicationCommand:
    """Bind public publication inputs to the invoking source repository."""

    snapshot: Path
    remote: str
    branch: str
    expected_sha: str
    source_repository: Path


class CliApplication(Protocol):
    """Expose command operations without leaking provider-specific inputs."""

    def collect(self, request: CollectionRequest) -> CollectionResult:
        """Build a complete candidate and return its safe terminal result."""
        ...

    def verify(self, snapshot: Path) -> SnapshotManifest:
        """Validate a snapshot and return its public manifest."""
        ...

    def publish(self, command: PublicationCommand) -> PublishResult:
        """Publish a validated snapshot under its immutable lease."""
        ...


@dataclass(frozen=True, slots=True)
class DefaultCliApplication:
    """Adapt the production collector, snapshot repository, and Git publisher."""

    git_runner: GitRunner = run_git

    def collect(self, request: CollectionRequest) -> CollectionResult:
        """Run collection through AnyIO at the synchronous CLI boundary."""
        return anyio.run(collect_snapshot, request)

    def verify(self, snapshot: Path) -> SnapshotManifest:
        """Load the complete tree before returning its manifest."""
        validated = SnapshotRepository(snapshot).read_optional()
        if validated is None:
            raise SnapshotIntegrityError(SnapshotIntegrityCategory.INVENTORY)
        return validated.manifest

    def publish(self, command: PublicationCommand) -> PublishResult:
        """Precheck and materialize the exact lease before publication."""
        if command.branch != DIST_BRANCH:
            raise InvalidPublicationError(InvalidPublicationCategory.EXPECTED_SHA)
        remote_url = _checked_git(
            self.git_runner,
            command.source_repository,
            ("remote", "get-url", command.remote),
        )
        with tempfile.TemporaryDirectory(prefix="cli-publish-baseline-") as temporary:
            repository = Path(temporary) / "repository"
            repository.mkdir()
            _ = _checked_git(self.git_runner, repository, ("init", "--quiet"))
            _ = _checked_git(
                self.git_runner,
                repository,
                ("remote", "add", "publish", remote_url),
            )
            advertised = _checked_git(
                self.git_runner,
                repository,
                ("ls-remote", "--heads", "publish", _DIST_REF),
            )
            expected = (
                ""
                if command.expected_sha == _ABSENT
                else f"{command.expected_sha}\t{_DIST_REF}"
            )
            if advertised != expected:
                raise StalePublicationError(command.expected_sha)
            previous = _materialize_previous(self.git_runner, repository, command)
            return publish_snapshot(
                PublishRequest(
                    command.source_repository,
                    command.snapshot,
                    previous,
                    command.remote,
                    command.expected_sha,
                ),
                runner=self.git_runner,
            )


def _checked_git(runner: GitRunner, cwd: Path, arguments: tuple[str, ...]) -> str:
    result = runner(arguments, cwd, 30.0)
    if result.returncode != 0:
        raise GitCommandError(arguments, result.returncode)
    return result.stdout.strip()


def _materialize_previous(
    runner: GitRunner,
    repository: Path,
    command: PublicationCommand,
) -> Path | None:
    if command.expected_sha == _ABSENT:
        return None
    _ = _checked_git(
        runner,
        repository,
        ("fetch", "--quiet", "--depth=1", "publish", command.expected_sha),
    )
    _ = _checked_git(runner, repository, ("read-tree", command.expected_sha))
    baseline = repository.parent / "baseline"
    baseline.mkdir()
    _ = _checked_git(
        runner,
        repository,
        ("checkout-index", "--all", f"--prefix={baseline.resolve()}/"),
    )
    if SnapshotRepository(baseline).load_optional() is None:
        raise SnapshotIntegrityError(SnapshotIntegrityCategory.INVENTORY)
    return baseline
