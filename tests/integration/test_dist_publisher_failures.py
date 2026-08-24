from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest

from social_media_subscriber.publishing import git as publishing_git
from social_media_subscriber.publishing.git import (
    InvalidPublicationError,
    Published,
    StalePublicationError,
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
    SnapshotIntegrityCategory,
    SnapshotIntegrityError,
    SnapshotRepository,
)
from tests.integration.test_dist_publisher import (
    RecordingRunner,
    account_url,
    extract_archive,
    remote_has_dist,
    remote_sha,
    request,
    setup_repositories,
    snapshot,
)
from tests.unit.test_storage_repository import tree_bytes

if TYPE_CHECKING:
    from social_media_subscriber.storage.safe_tree import DirectoryTree
    from social_media_subscriber.storage.snapshot import SnapshotState, SnapshotSummary


def test_absent_lease_fails_when_dist_appeared_before_publication(
    tmp_path: Path,
) -> None:
    # Given
    source, remote = setup_repositories(tmp_path)
    first_candidate = tmp_path / "first"
    stale_candidate = tmp_path / "stale"
    snapshot(first_candidate, "3401")
    snapshot(stale_candidate, "3501")
    competing = publish_snapshot(request(source, first_candidate, "absent"))
    assert isinstance(competing, Published)
    runner = RecordingRunner()

    # When / Then
    with pytest.raises(StalePublicationError):
        _ = publish_snapshot(request(source, stale_candidate, "absent"), runner=runner)
    assert remote_sha(remote) == competing.sha
    competing_tree = tmp_path / "competing"
    extract_archive(remote, competing.sha, competing_tree)
    competing_state = SnapshotRepository(competing_tree).load_optional()
    assert competing_state is not None
    assert tuple(str(account.id) for account in competing_state.accounts) == (
        account_url("3401"),
    )
    pushes = [command for command in runner.commands if command[0] == "push"]
    assert pushes == []


@pytest.mark.parametrize("expected_sha", ["", "ABSENT", "abc", "g" * 40])
def test_malformed_expected_sha_is_rejected(tmp_path: Path, expected_sha: str) -> None:
    # Given
    source, _ = setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    snapshot(candidate)

    # When / Then
    with pytest.raises(InvalidPublicationError):
        _ = publish_snapshot(request(source, candidate, expected_sha))


def test_absent_baseline_rejects_a_previous_snapshot(tmp_path: Path) -> None:
    # Given
    source, _ = setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    previous = tmp_path / "previous"
    snapshot(candidate)
    snapshot(previous)

    # When / Then
    with pytest.raises(InvalidPublicationError):
        _ = publish_snapshot(request(source, candidate, "absent", previous))


def test_previous_snapshot_must_match_the_observed_commit(tmp_path: Path) -> None:
    # Given
    source, remote = setup_repositories(tmp_path)
    candidate_a = tmp_path / "candidate-a"
    mismatched = tmp_path / "mismatched"
    snapshot(candidate_a)
    snapshot(mismatched, "4001")
    first = publish_snapshot(request(source, candidate_a, "absent"))
    assert isinstance(first, Published)

    # When / Then
    with pytest.raises(InvalidPublicationError):
        _ = publish_snapshot(request(source, mismatched, first.sha, mismatched))
    assert remote_sha(remote) == first.sha


def test_corrupt_candidate_is_rejected_before_git_publication(tmp_path: Path) -> None:
    # Given
    source, remote = setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    snapshot(candidate)
    _ = (candidate / "snapshot.json").write_bytes(b"{}\n")

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = publish_snapshot(request(source, candidate, "absent"))
    assert not remote_has_dist(remote)


def test_candidate_root_replacement_after_validation_is_rejected_before_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    validated = tmp_path / "validated-candidate"
    outside = tmp_path / "outside"
    snapshot(candidate)
    candidate_bytes = tree_bytes(candidate)
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    _ = sentinel.write_bytes(b"external sentinel")
    original_read_bytes = Path.read_bytes
    replaced = False
    external_read = False

    class ReplacingRepository(SnapshotRepository):
        @override
        def _load_tree(
            self, tree: DirectoryTree
        ) -> tuple[SnapshotState, SnapshotSummary]:
            nonlocal replaced
            result = super()._load_tree(tree)
            if not replaced:
                replaced = True
                _ = candidate.rename(validated)
                candidate.symlink_to(outside, target_is_directory=True)
            return result

    def guarded_read_bytes(path: Path) -> bytes:
        nonlocal external_read
        if path == candidate / sentinel.name:
            external_read = True
            message = "publisher reopened the replaced snapshot path"
            raise AssertionError(message)
        return original_read_bytes(path)

    monkeypatch.setattr(publishing_git, "SnapshotRepository", ReplacingRepository)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    runner = RecordingRunner()

    with pytest.raises(SnapshotIntegrityError) as raised:
        _ = publish_snapshot(request(source, candidate, "absent"), runner=runner)

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert external_read is False
    assert candidate.is_symlink()
    assert original_read_bytes(sentinel) == b"external sentinel"
    assert tree_bytes(validated) == candidate_bytes
    assert runner.commands == []
    assert [command for command in runner.commands if command[0] == "push"] == []


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
    source, _ = setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    snapshot(candidate)
    runner: GitRunner = _FailingRunner(
        GitCommandResult(1, "Everything up-to-date\n", "injected failure\n")
    )

    # When / Then
    with pytest.raises(GitCommandError):
        _ = publish_snapshot(request(source, candidate, "absent"), runner=runner)


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
    source, remote = setup_repositories(tmp_path)
    candidate = tmp_path / "candidate"
    snapshot(candidate)
    runner: GitRunner = _InterruptingRunner()

    # When / Then
    with pytest.raises(GitInterruptedError):
        _ = publish_snapshot(request(source, candidate, "absent"), runner=runner)
    assert not remote_has_dist(remote)


def test_default_runner_reports_a_real_timeout(tmp_path: Path) -> None:
    # Given / When / Then
    with pytest.raises(GitInterruptedError):
        _ = run_git(("-c", "alias.hang=!sleep 2", "hang"), tmp_path, 0.01)
