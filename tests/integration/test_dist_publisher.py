from __future__ import annotations

import hashlib
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from social_media_subscriber.domain import (
    Account,
    AccountKind,
    Platform,
    PlatformAccountId,
)
from social_media_subscriber.domain.ids import account_id_for
from social_media_subscriber.publishing.git import (
    Published,
    PublishRequest,
    Unchanged,
    publish_snapshot,
)
from social_media_subscriber.publishing.process import (
    GitCommandResult,
    run_git,
)
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.snapshot import SnapshotState


def _git(cwd: Path, *arguments: str) -> str:
    result = run_git(arguments, cwd, 10.0)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _snapshot(root: Path, account_suffix: str | None = None) -> None:
    if account_suffix is None:
        state = SnapshotState((), (), ())
    else:
        platform_id = PlatformAccountId(account_suffix)
        account = Account(
            id=account_id_for(AccountKind.PERSON, platform_id),
            platform=Platform.LINKEDIN,
            kind=AccountKind.PERSON,
            platform_account_id=platform_id,
            profile_url=f"https://www.linkedin.com/in/synthetic-{account_suffix}/",
            url_aliases=(),
            first_seen_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
        )
        state = SnapshotState((account,), (), ())
    _ = SnapshotRepository(root).write(state)


def _setup_repositories(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    source.mkdir()
    _ = _git(source, "init", "--initial-branch=master")
    _ = _git(source, "config", "user.name", "Synthetic Source")
    _ = _git(source, "config", "user.email", "synthetic@example.invalid")
    _ = (source / "README.md").write_text("source\n")
    _ = _git(source, "add", "README.md")
    _ = _git(source, "commit", "-m", "source baseline")
    _ = _git(tmp_path, "init", "--bare", str(remote))
    _ = _git(source, "remote", "add", "origin", str(remote))
    return source, remote


def _request(
    source: Path,
    snapshot: Path,
    expected_sha: str,
    previous: Path | None = None,
) -> PublishRequest:
    return PublishRequest(
        source_repository=source,
        snapshot=snapshot,
        previous_snapshot=previous,
        remote="origin",
        expected_sha=expected_sha,
    )


def _remote_sha(remote: Path) -> str:
    return _git(remote, "rev-parse", "refs/heads/dist")


def _remote_has_dist(remote: Path) -> bool:
    result = run_git(
        ("show-ref", "--verify", "--quiet", "refs/heads/dist"), remote, 10.0
    )
    return result.returncode == 0


def _extract(remote: Path, sha: str, destination: Path) -> None:
    archive = destination.parent / f"{destination.name}.tar"
    destination.mkdir()
    _ = _git(remote, "archive", "--format=tar", f"--output={archive}", sha)
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _git_fingerprint(source: Path) -> tuple[str, str, str, str]:
    git_dir = Path(_git(source, "rev-parse", "--absolute-git-dir"))
    git_bytes = hashlib.sha256()
    for path in sorted(git_dir.rglob("*")):
        if path.is_file():
            git_bytes.update(path.relative_to(git_dir).as_posix().encode())
            git_bytes.update(b"\0")
            git_bytes.update(path.read_bytes())
    return (
        str(git_dir),
        _git(source, "rev-parse", "HEAD"),
        _git(source, "status", "--porcelain=v1", "--untracked-files=all"),
        git_bytes.hexdigest(),
    )


@dataclass(slots=True)
class _RecordingRunner:
    commands: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(
        self, arguments: tuple[str, ...], cwd: Path, timeout_seconds: float
    ) -> GitCommandResult:
        self.commands.append(arguments)
        return run_git(arguments, cwd, timeout_seconds)


def test_initial_changed_and_unchanged_publications_use_one_root_commit(
    tmp_path: Path,
) -> None:
    # Given
    source, remote = _setup_repositories(tmp_path)
    candidate_a = tmp_path / "candidate-a"
    candidate_b = tmp_path / "candidate-b"
    prior_a = tmp_path / "prior-a"
    prior_b = tmp_path / "prior-b"
    _snapshot(candidate_a)
    _snapshot(candidate_b, "1001")
    runner = _RecordingRunner()

    # When
    first = publish_snapshot(_request(source, candidate_a, "absent"), runner=runner)
    assert isinstance(first, Published)
    first_sha = _remote_sha(remote)
    _extract(remote, first_sha, prior_a)
    second = publish_snapshot(
        _request(source, candidate_b, first_sha, prior_a), runner=runner
    )
    assert isinstance(second, Published)
    second_sha = _remote_sha(remote)
    _extract(remote, second_sha, prior_b)
    third = publish_snapshot(
        _request(source, candidate_b, second_sha, prior_b), runner=runner
    )

    # Then
    assert first.sha == first_sha
    assert second.sha == second_sha
    assert first_sha != second_sha
    assert isinstance(third, Unchanged)
    assert third.sha == second_sha == _remote_sha(remote)
    assert _git(remote, "rev-list", "--count", "dist") == "1"
    assert _git(remote, "rev-list", "--parents", "dist").split() == [second_sha]
    assert _git(remote, "show", "-s", "--format=%an <%ae>", "dist") == (
        "social-media-subscriber[bot] "
        "<social-media-subscriber[bot]@users.noreply.github.com>"
    )
    assert _tree(prior_b) == _tree(candidate_b)
    pushes = [command for command in runner.commands if command[0] == "push"]
    assert len(pushes) == 2
    assert pushes[0][1] == "--force-with-lease=refs/heads/dist:"
    assert pushes[1][1] == f"--force-with-lease=refs/heads/dist:{first_sha}"


RecordingRunner = _RecordingRunner
extract_archive = _extract
git_fingerprint = _git_fingerprint
git_for_test = _git
remote_has_dist = _remote_has_dist
remote_sha = _remote_sha
request = _request
setup_repositories = _setup_repositories
snapshot = _snapshot
