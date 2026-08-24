from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

import pytest
from pydantic import TypeAdapter
from typer.testing import CliRunner

from social_media_subscriber.cli import create_app
from social_media_subscriber.cli_application import DefaultCliApplication
from social_media_subscriber.publishing.process import GitCommandResult, run_git
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from typer import Typer

_GIT: Final = shutil.which("git")


class CliResult(Protocol):
    @property
    def output(self) -> str: ...

    @property
    def exit_code(self) -> int: ...

    @property
    def stdout(self) -> str: ...


_REPORT_ADAPTER = TypeAdapter(dict[str, str | int | None])


def _git_binary() -> str:
    if _GIT is None:
        pytest.fail("git executable required")
    return _GIT


def _git(cwd: Path, *arguments: str) -> str:
    completed = subprocess.run(  # noqa: S603 - executable resolved before invocation
        (_git_binary(), *arguments),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _report(output: str) -> dict[str, str | int | None]:
    return _REPORT_ADAPTER.validate_json(
        next(line for line in output.splitlines() if line.startswith("{"))
    )


def _setup_source(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    source.mkdir()
    remote.mkdir()
    _ = _git(source, "init", "--quiet")
    _ = _git(remote, "init", "--quiet", "--bare")
    _ = _git(source, "remote", "add", "origin", str(remote))
    return source, remote


@dataclass(frozen=True, slots=True)
class ContainedCli:
    root: Path
    source: Path
    remote: Path
    runner: CliRunner

    def invoke(self, app: Typer, arguments: list[str]) -> CliResult:
        assert Path.cwd().resolve() == self.source.resolve()
        assert self.source.resolve().is_relative_to(self.root.resolve())
        remote_url = _git(self.source, "remote", "get-url", "origin")
        assert "://" not in remote_url
        resolved_remote = (self.source / remote_url).resolve()
        assert resolved_remote == self.remote.resolve()
        assert resolved_remote.is_relative_to(self.root.resolve())
        return self.runner.invoke(app, arguments, catch_exceptions=False)


@dataclass(slots=True)
class AdvancingRunner:
    remote: Path
    advanced: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(
        self, arguments: tuple[str, ...], cwd: Path, timeout_seconds: float
    ) -> GitCommandResult:
        self.calls.append(arguments)
        result = run_git(arguments, cwd, timeout_seconds)
        if arguments[:2] == ("ls-remote", "--heads") and not self.advanced:
            self._advance_remote()
            self.advanced = True
        return result

    def _advance_remote(self) -> None:
        competitor = self.remote.parent / "competitor"
        competitor.mkdir()
        snapshot = competitor / "snapshot"
        _ = SnapshotRepository(snapshot).write(SnapshotState((), ()))
        _ = _git(competitor, "init", "--quiet")
        _ = _git(competitor, "add", "snapshot")
        _ = _git(
            competitor,
            "-c",
            "user.name=contained-test",
            "-c",
            "user.email=contained@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Competing publication",
        )
        _ = _git(competitor, "remote", "add", "contained", str(self.remote))
        _ = _git(competitor, "push", "--quiet", "contained", "HEAD:refs/heads/dist")


@pytest.fixture
def contained_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ContainedCli:
    source, remote = _setup_source(tmp_path)
    monkeypatch.chdir(source)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    contained = ContainedCli(tmp_path, source, remote, CliRunner())
    assert Path.cwd().resolve() == source.resolve()
    assert (
        Path(_git(source, "remote", "get-url", "origin")).resolve() == remote.resolve()
    )
    return contained


def test_verify_snapshot_maps_invalid_tree_to_integrity_exit(tmp_path: Path) -> None:
    # Given
    invalid = tmp_path / "invalid"
    invalid.mkdir()

    # When
    result = CliRunner().invoke(create_app(), ["verify-snapshot", str(invalid)])

    # Then
    assert result.exit_code == 5
    assert _report(result.output) == {
        "command": "verify-snapshot",
        "error_category": "integrity",
        "exit_code": 5,
    }


def test_publish_materializes_exact_baseline_and_preserves_unchanged_sha(
    tmp_path: Path, contained_cli: ContainedCli
) -> None:
    # Given
    snapshot = tmp_path / "snapshot"
    _ = SnapshotRepository(snapshot).write(SnapshotState((), ()))
    arguments = [
        "publish-dist",
        "--snapshot",
        str(snapshot),
        "--remote",
        "origin",
        "--branch",
        "dist",
        "--expected-sha",
    ]

    # When
    first = contained_cli.invoke(create_app(), [*arguments, "absent"])
    first_sha = _report(first.stdout)["sha"]
    second = contained_cli.invoke(create_app(), [*arguments, str(first_sha)])

    # Then
    assert first.exit_code == second.exit_code == 0
    assert _report(first.stdout)["result"] == "published"
    assert _report(second.stdout) == {
        "command": "publish-dist",
        "exit_code": 0,
        "result": "unchanged",
        "sha": first_sha,
    }
    assert _git(contained_cli.remote, "rev-parse", "refs/heads/dist") == first_sha
    assert not list(tmp_path.glob("snapshot-publish-*"))
    assert not list(tmp_path.glob("cli-publish-baseline-*"))


def test_publish_stale_precheck_exits_six_without_source_mutation(
    tmp_path: Path, contained_cli: ContainedCli
) -> None:
    # Given
    snapshot = tmp_path / "snapshot"
    _ = SnapshotRepository(snapshot).write(SnapshotState((), ()))
    before = tuple(contained_cli.source.iterdir())

    # When
    result = contained_cli.invoke(
        create_app(),
        [
            "publish-dist",
            "--snapshot",
            str(snapshot),
            "--remote",
            "origin",
            "--branch",
            "dist",
            "--expected-sha",
            "0" * 40,
        ],
    )

    # Then
    assert result.exit_code == 6
    assert _report(result.stdout)["exit_code"] == 6
    assert tuple(contained_cli.source.iterdir()) == before
    advertised = subprocess.run(  # noqa: S603 - executable resolved before invocation
        (_git_binary(), "show-ref"),
        cwd=contained_cli.remote,
        check=False,
        capture_output=True,
        text=True,
    )
    assert advertised.returncode == 1
    assert advertised.stdout == ""


def test_containment_guard_blocks_wrong_cwd_before_cli_invocation(
    contained_cli: ContainedCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.chdir(contained_cli.root)
    arguments = [
        "publish-dist",
        "--snapshot",
        "candidate",
        "--expected-sha",
        "absent",
    ]

    # When
    with pytest.raises(AssertionError):
        _ = contained_cli.invoke(create_app(), arguments)

    # Then
    assert not (contained_cli.remote / "refs" / "heads" / "dist").exists()


def test_post_precheck_race_exits_six_without_force_fallback(
    tmp_path: Path,
    contained_cli: ContainedCli,
) -> None:
    # Given
    snapshot = tmp_path / "snapshot"
    _ = SnapshotRepository(snapshot).write(SnapshotState((), ()))
    runner = AdvancingRunner(contained_cli.remote)

    # When
    result = contained_cli.invoke(
        create_app(DefaultCliApplication(runner)),
        [
            "publish-dist",
            "--snapshot",
            str(snapshot),
            "--remote",
            "origin",
            "--branch",
            "dist",
            "--expected-sha",
            "absent",
        ],
    )

    # Then
    assert result.exit_code == 6
    assert runner.advanced is True
    assert not any(arguments[0] == "push" for arguments in runner.calls)
    assert not list(tmp_path.glob("snapshot-publish-*"))
    assert not list(tmp_path.glob("cli-publish-baseline-*"))
