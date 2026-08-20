"""Race-safe publication of validated snapshots to a one-commit dist branch."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, override

from social_media_subscriber.publishing.process import (
    GitCommandError,
    GitRunner,
    run_git,
)
from social_media_subscriber.storage.repository import SnapshotRepository

_ABSENT: Final = "absent"
_DIST_REF: Final = "refs/heads/dist"
_SHA_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_BOT_NAME: Final = "social-media-subscriber[bot]"
_BOT_EMAIL: Final = "social-media-subscriber[bot]@users.noreply.github.com"


class InvalidPublicationCategory(StrEnum):
    """Closed invalid-input and snapshot-baseline categories."""

    EXPECTED_SHA = "expected SHA"
    PREVIOUS_FOR_ABSENT = "previous snapshot supplied for absent baseline"
    PREVIOUS_REQUIRED = "previous snapshot required for observed baseline"
    PREVIOUS_MISMATCH = "previous snapshot does not match observed commit"
    SNAPSHOT_REQUIRED = "validated snapshot root required"
    TIMEOUT = "positive command timeout required"


@dataclass(frozen=True, slots=True)
class InvalidPublicationError(Exception):
    """Publication input cannot establish the immutable lease contract."""

    category: InvalidPublicationCategory

    @override
    def __str__(self) -> str:
        return f"invalid dist publication: {self.category}"


@dataclass(frozen=True, slots=True)
class StalePublicationError(Exception):
    """The immutable dist lease no longer matches the remote branch."""

    expected_sha: str

    @override
    def __str__(self) -> str:
        return f"stale dist publication lease: {self.expected_sha}"


@dataclass(frozen=True, slots=True)
class PublishRequest:
    """Inputs bound to one terminal publication attempt."""

    source_repository: Path
    snapshot: Path
    previous_snapshot: Path | None
    remote: str
    expected_sha: str
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class Published:
    """A changed validated snapshot replaced the remote dist root."""

    sha: str


@dataclass(frozen=True, slots=True)
class Unchanged:
    """The validated candidate matched the leased remote snapshot."""

    sha: str


type PublishResult = Published | Unchanged


def _checked(
    runner: GitRunner,
    arguments: tuple[str, ...],
    cwd: Path,
    timeout_seconds: float,
) -> str:
    result = runner(arguments, cwd, timeout_seconds)
    if result.returncode != 0:
        raise GitCommandError(arguments, result.returncode)
    return result.stdout.strip()


def _validated_tree(root: Path) -> dict[Path, bytes]:
    state = SnapshotRepository(root).load_optional()
    if state is None:
        raise InvalidPublicationError(InvalidPublicationCategory.SNAPSHOT_REQUIRED)
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _materialize(tree: dict[Path, bytes], destination: Path) -> None:
    for relative_path, payload in tree.items():
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_bytes(payload)


def _write_tree(
    runner: GitRunner,
    repository: Path,
    tree: dict[Path, bytes],
    timeout_seconds: float,
) -> str:
    _materialize(tree, repository)
    _ = _checked(runner, ("add", "--all"), repository, timeout_seconds)
    return _checked(runner, ("write-tree",), repository, timeout_seconds)


def _validate_previous_tree(
    runner: GitRunner,
    repository: Path,
    request: PublishRequest,
    remote_url: str,
    previous_tree: dict[Path, bytes],
) -> None:
    expected_sha = request.expected_sha
    timeout_seconds = request.timeout_seconds
    _ = _checked(runner, ("init", "--quiet"), repository, timeout_seconds)
    _ = _checked(
        runner, ("remote", "add", "publish", remote_url), repository, timeout_seconds
    )
    _ = _checked(
        runner,
        ("fetch", "--quiet", "--depth=1", "publish", expected_sha),
        repository,
        timeout_seconds,
    )
    observed_tree = _checked(
        runner, ("rev-parse", f"{expected_sha}^{{tree}}"), repository, timeout_seconds
    )
    supplied_tree = _write_tree(runner, repository, previous_tree, timeout_seconds)
    if supplied_tree != observed_tree:
        raise InvalidPublicationError(InvalidPublicationCategory.PREVIOUS_MISMATCH)


def _verify_remote_lease(
    runner: GitRunner,
    repository: Path,
    expected_sha: str,
    timeout_seconds: float,
) -> None:
    advertised = _checked(
        runner,
        ("ls-remote", "--heads", "publish", _DIST_REF),
        repository,
        timeout_seconds,
    )
    fields = advertised.split()
    expected_fields = [] if expected_sha == _ABSENT else [expected_sha, _DIST_REF]
    if fields != expected_fields:
        raise StalePublicationError(expected_sha)


def publish_snapshot(
    request: PublishRequest, *, runner: GitRunner = run_git
) -> PublishResult:
    """Publish one validated candidate using only its immutable observed lease."""
    if request.timeout_seconds <= 0:
        raise InvalidPublicationError(InvalidPublicationCategory.TIMEOUT)
    expected_sha = request.expected_sha
    if expected_sha != _ABSENT and _SHA_PATTERN.fullmatch(expected_sha) is None:
        raise InvalidPublicationError(InvalidPublicationCategory.EXPECTED_SHA)
    candidate_tree = _validated_tree(request.snapshot)
    remote_url = _checked(
        runner,
        ("remote", "get-url", request.remote),
        request.source_repository,
        request.timeout_seconds,
    )
    previous_tree: dict[Path, bytes] | None = None
    if expected_sha == _ABSENT:
        if request.previous_snapshot is not None:
            raise InvalidPublicationError(
                InvalidPublicationCategory.PREVIOUS_FOR_ABSENT
            )
    else:
        if request.previous_snapshot is None:
            raise InvalidPublicationError(InvalidPublicationCategory.PREVIOUS_REQUIRED)
        previous_tree = _validated_tree(request.previous_snapshot)

    with tempfile.TemporaryDirectory(prefix="snapshot-publish-") as temporary:
        temporary_root = Path(temporary)
        if previous_tree is not None:
            validation_repository = temporary_root / "validation"
            validation_repository.mkdir()
            _validate_previous_tree(
                runner,
                validation_repository,
                request,
                remote_url,
                previous_tree,
            )
            if candidate_tree == previous_tree:
                _verify_remote_lease(
                    runner,
                    validation_repository,
                    expected_sha,
                    request.timeout_seconds,
                )
                return Unchanged(expected_sha)

        publication_repository = temporary_root / "publication"
        publication_repository.mkdir()
        _ = _checked(
            runner,
            ("init", "--quiet", "--initial-branch=dist"),
            publication_repository,
            request.timeout_seconds,
        )
        _ = _checked(
            runner,
            ("remote", "add", "publish", remote_url),
            publication_repository,
            request.timeout_seconds,
        )
        _verify_remote_lease(
            runner,
            publication_repository,
            expected_sha,
            request.timeout_seconds,
        )
        tree_sha = _write_tree(
            runner, publication_repository, candidate_tree, request.timeout_seconds
        )
        commit_sha = _checked(
            runner,
            (
                "-c",
                f"user.name={_BOT_NAME}",
                "-c",
                f"user.email={_BOT_EMAIL}",
                "commit-tree",
                tree_sha,
                "-m",
                "Publish validated snapshot",
            ),
            publication_repository,
            request.timeout_seconds,
        )
        lease_sha = "" if expected_sha == _ABSENT else expected_sha
        push = runner(
            (
                "push",
                f"--force-with-lease={_DIST_REF}:{lease_sha}",
                "publish",
                f"{commit_sha}:{_DIST_REF}",
            ),
            publication_repository,
            request.timeout_seconds,
        )
        if push.returncode != 0:
            raise StalePublicationError(expected_sha)
        return Published(commit_sha)
