from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.publishing.git import (
    Published,
    PublishRequest,
    StalePublicationError,
    publish_snapshot,
)
from social_media_subscriber.publishing.process import GitCommandResult, run_git
from tests.integration.test_dist_publisher import (
    RecordingRunner,
    extract_archive,
    git_fingerprint,
    git_for_test,
    remote_sha,
    request,
    setup_repositories,
    snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_stale_writer_preserves_competing_remote_and_source_repository(
    tmp_path: Path,
) -> None:
    # Given
    source, remote = setup_repositories(tmp_path)
    candidate_a = tmp_path / "candidate-a"
    candidate_b = tmp_path / "candidate-b"
    candidate_c = tmp_path / "candidate-c"
    prior_a = tmp_path / "prior-a"
    snapshot(candidate_a)
    snapshot(candidate_b, "2001")
    snapshot(candidate_c, "3001")
    first = publish_snapshot(request(source, candidate_a, "absent"))
    assert isinstance(first, Published)
    extract_archive(remote, first.sha, prior_a)
    competing = publish_snapshot(request(source, candidate_b, first.sha, prior_a))
    assert isinstance(competing, Published)
    _ = (source / "README.md").write_text("dirty source\n")
    _ = (source / "staged.txt").write_text("staged\n")
    _ = git_for_test(source, "add", "staged.txt")
    omo = source / ".omo" / "proof.bin"
    omo.parent.mkdir()
    _ = omo.write_bytes(b"preserve me\x00")
    before = git_fingerprint(source)
    runner = RecordingRunner()

    # When / Then
    with pytest.raises(StalePublicationError):
        _ = publish_snapshot(
            request(source, candidate_c, first.sha, prior_a), runner=runner
        )
    assert remote_sha(remote) == competing.sha
    assert git_fingerprint(source) == before
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
    source, remote = setup_repositories(tmp_path)
    candidate_a = tmp_path / "candidate-a"
    candidate_b = tmp_path / "candidate-b"
    candidate_c = tmp_path / "candidate-c"
    prior_a = tmp_path / "prior-a"
    snapshot(candidate_a)
    snapshot(candidate_b, "3101")
    snapshot(candidate_c, "3201")
    first = publish_snapshot(request(source, candidate_a, "absent"))
    assert isinstance(first, Published)
    extract_archive(remote, first.sha, prior_a)
    runner = _AdvancingRunner(request(source, candidate_b, first.sha, prior_a))

    # When / Then
    with pytest.raises(StalePublicationError):
        _ = publish_snapshot(
            request(source, candidate_c, first.sha, prior_a), runner=runner
        )
    assert runner.competing_sha is not None
    assert remote_sha(remote) == runner.competing_sha
    pushes = [command for command in runner.commands if command[0] == "push"]
    assert len(pushes) == 1
    assert pushes[0][1] == f"--force-with-lease=refs/heads/dist:{first.sha}"
    assert "--force" not in pushes[0]
