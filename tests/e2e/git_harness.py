from __future__ import annotations

import shutil
import subprocess
from contextlib import chdir
from typing import TYPE_CHECKING, Final, Protocol

from typer.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

    from typer import Typer

_GIT: Final = shutil.which("git")
_RUNNER: Final = CliRunner()


class CliResult(Protocol):
    @property
    def exit_code(self) -> int: ...

    @property
    def output(self) -> str: ...


def git(cwd: Path, *arguments: str) -> str:
    assert _GIT is not None
    completed = subprocess.run(  # noqa: S603 - executable resolved before invocation
        (_GIT, *arguments),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def publish(
    app: Typer,
    source: Path,
    snapshot: Path,
    expected_sha: str,
) -> CliResult:
    with chdir(source):
        return _RUNNER.invoke(
            app,
            [
                "publish-dist",
                "--snapshot",
                str(snapshot),
                "--remote",
                "origin",
                "--expected-sha",
                expected_sha,
            ],
            catch_exceptions=False,
        )


def publication_root(root: Path) -> tuple[Path, Path]:
    source = root / "source-repository"
    remote = root / "origin.git"
    source.mkdir()
    remote.mkdir()
    _ = git(source, "init", "--quiet")
    _ = git(remote, "init", "--quiet", "--bare")
    _ = git(source, "remote", "add", "origin", str(remote))
    assert "://" not in git(source, "remote", "get-url", "origin")
    assert remote.resolve().is_relative_to(root.resolve())
    return source, remote
