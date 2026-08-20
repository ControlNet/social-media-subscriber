from __future__ import annotations

import hashlib
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from social_media_subscriber.domain import (
    Account,
    AccountKind,
    Platform,
    PlatformAccountId,
)
from social_media_subscriber.domain.ids import account_id_for
from social_media_subscriber.publishing.git import (
    InvalidPublicationError,
    Published,
    PublishRequest,
    StalePublicationError,
    Unchanged,
    publish_snapshot,
)
from social_media_subscriber.publishing.process import (
    GitCommandError,
    GitCommandResult,
    GitInterruptedError,
    GitRunner,
    run_git,
)
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
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


def test_stale_writer_preserves_competing_remote_and_source_repository(
    tmp_path: Path,
) -> None:
    # Given
    source, remote = _setup_repositories(tmp_path)
    candidate_a = tmp_path / "candidate-a"
    candidate_b = tmp_path / "candidate-b"
    candidate_c = tmp_path / "candidate-c"
    prior_a = tmp_path / "prior-a"
    _snapshot(candidate_a)
    _snapshot(candidate_b, "2001")
    _snapshot(candidate_c, "3001")
    first = publish_snapshot(_request(source, candidate_a, "absent"))
    assert isinstance(first, Published)
    _extract(remote, first.sha, prior_a)
    competing = publish_snapshot(_request(source, candidate_b, first.sha, prior_a))
    assert isinstance(competing, Published)
    _ = (source / "README.md").write_text("dirty source\n")
    _ = (source / "staged.txt").write_text("staged\n")
    _ = _git(source, "add", "staged.txt")
    omo = source / ".omo" / "proof.bin"
    omo.parent.mkdir()
    _ = omo.write_bytes(b"preserve me\x00")
    before = _git_fingerprint(source)
    runner = _RecordingRunner()

    # When / Then
    with pytest.raises(StalePublicationError):
        _ = publish_snapshot(
            _request(source, candidate_c, first.sha, prior_a), runner=runner
        )
    assert _remote_sha(remote) == competing.sha
    assert _git_fingerprint(source) == before
    assert omo.read_bytes() == b"preserve me\x00"
    pushes = [command for command in runner.commands if command[0] == "push"]
    assert pushes == []


@dataclass(slots=True)
class _AdvancingRunner:
    competing_request: PublishRequest
    commands: list[tuple[str, ...]] = field(default_factory=list)
    competing_sha: str | None = None

    def __call__(
        self, arguments: tuple[str, ...], cwd: Path, timeout_seconds: float
    ) -> GitCommandResult:
        self.commands.append(arguments)
        if arguments[0] == "push" and self.competing_sha is None:
            competing = publish_snapshot(self.competing_request)
            assert isinstance(competing, Published)
            self.competing_sha = competing.sha
        return run_git(arguments, cwd, timeout_seconds)


def test_remote_advancement_after_precheck_is_stopped_by_the_push_lease(
    tmp_path: Path,
) -> None:
    # Given
    source, remote = _setup_repositories(tmp_path)
    candidate_a = tmp_path / "candidate-a"
    candidate_b = tmp_path / "candidate-b"
    candidate_c = tmp_path / "candidate-c"
    prior_a = tmp_path / "prior-a"
    _snapshot(candidate_a)
    _snapshot(candidate_b, "3101")
    _snapshot(candidate_c, "3201")
    first = publish_snapshot(_request(source, candidate_a, "absent"))
    assert isinstance(first, Published)
    _extract(remote, first.sha, prior_a)
    runner = _AdvancingRunner(_request(source, candidate_b, first.sha, prior_a))

    # When / Then
    with pytest.raises(StalePublicationError):
        _ = publish_snapshot(
            _request(source, candidate_c, first.sha, prior_a), runner=runner
        )
    assert runner.competing_sha is not None
    assert _remote_sha(remote) == runner.competing_sha
    pushes = [command for command in runner.commands if command[0] == "push"]
    assert len(pushes) == 1
    assert pushes[0][1] == f"--force-with-lease=refs/heads/dist:{first.sha}"
    assert "--force" not in pushes[0]


def test_absent_lease_fails_when_dist_appeared_before_publication(
    tmp_path: Path,
) -> None:
    # Given
    source, remote = _setup_repositories(tmp_path)
    first_candidate = tmp_path / "first"
    stale_candidate = tmp_path / "stale"
    _snapshot(first_candidate)
    _snapshot(stale_candidate, "3501")
    competing = publish_snapshot(_request(source, first_candidate, "absent"))
    assert isinstance(competing, Published)
    runner = _RecordingRunner()

    # When / Then
    with pytest.raises(StalePublicationError):
        _ = publish_snapshot(_request(source, stale_candidate, "absent"), runner=runner)
    assert _remote_sha(remote) == competing.sha
    pushes = [command for command in runner.commands if command[0] == "push"]
    assert pushes == []


@pytest.mark.parametrize("expected_sha", ["", "ABSENT", "abc", "g" * 40])
def test_malformed_expected_sha_is_rejected(tmp_path: Path, expected_sha: str) -> None:
    # Given
    source, _ = _setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    _snapshot(candidate)

    # When / Then
    with pytest.raises(InvalidPublicationError):
        _ = publish_snapshot(_request(source, candidate, expected_sha))


def test_absent_baseline_rejects_a_previous_snapshot(tmp_path: Path) -> None:
    # Given
    source, _ = _setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    previous = tmp_path / "previous"
    _snapshot(candidate)
    _snapshot(previous)

    # When / Then
    with pytest.raises(InvalidPublicationError):
        _ = publish_snapshot(_request(source, candidate, "absent", previous))


def test_previous_snapshot_must_match_the_observed_commit(tmp_path: Path) -> None:
    # Given
    source, remote = _setup_repositories(tmp_path)
    candidate_a = tmp_path / "candidate-a"
    mismatched = tmp_path / "mismatched"
    _snapshot(candidate_a)
    _snapshot(mismatched, "4001")
    first = publish_snapshot(_request(source, candidate_a, "absent"))
    assert isinstance(first, Published)

    # When / Then
    with pytest.raises(InvalidPublicationError):
        _ = publish_snapshot(_request(source, mismatched, first.sha, mismatched))
    assert _remote_sha(remote) == first.sha


def test_corrupt_candidate_is_rejected_before_git_publication(tmp_path: Path) -> None:
    # Given
    source, remote = _setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    _snapshot(candidate)
    _ = (candidate / "snapshot.json").write_bytes(b"{}\n")

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = publish_snapshot(_request(source, candidate, "absent"))
    assert not _remote_has_dist(remote)


@dataclass(slots=True)
class _FailingRunner:
    result: GitCommandResult

    def __call__(
        self, arguments: tuple[str, ...], cwd: Path, timeout_seconds: float
    ) -> GitCommandResult:
        _ = arguments, cwd, timeout_seconds
        return self.result


def test_nonzero_git_result_is_failure_even_with_success_stdout(tmp_path: Path) -> None:
    # Given
    source, _ = _setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    _snapshot(candidate)
    runner: GitRunner = _FailingRunner(
        GitCommandResult(1, "Everything up-to-date\n", "injected failure\n")
    )

    # When / Then
    with pytest.raises(GitCommandError):
        _ = publish_snapshot(_request(source, candidate, "absent"), runner=runner)


@dataclass(slots=True)
class _InterruptingRunner:
    def __call__(
        self, arguments: tuple[str, ...], cwd: Path, timeout_seconds: float
    ) -> GitCommandResult:
        raise GitInterruptedError(arguments, cwd, timeout_seconds)


def test_interrupted_git_command_is_typed_and_remote_is_untouched(
    tmp_path: Path,
) -> None:
    # Given
    source, remote = _setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    _snapshot(candidate)
    runner: GitRunner = _InterruptingRunner()

    # When / Then
    with pytest.raises(GitInterruptedError):
        _ = publish_snapshot(_request(source, candidate, "absent"), runner=runner)
    assert not _remote_has_dist(remote)


def test_default_runner_reports_a_real_timeout(tmp_path: Path) -> None:
    # Given / When / Then
    with pytest.raises(GitInterruptedError):
        _ = run_git(("-c", "alias.hang=!sleep 2", "hang"), tmp_path, 0.01)
