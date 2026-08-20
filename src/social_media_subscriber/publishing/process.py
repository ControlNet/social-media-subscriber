"""Typed, bounded Git subprocess boundary."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, override

if TYPE_CHECKING:
    from pathlib import Path

_GIT_EXECUTABLE: Final = shutil.which("git")


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    """Captured process status without trusting command output as success."""

    returncode: int
    stdout: str
    stderr: str


class GitRunner(Protocol):
    """Narrow injectable boundary for Git subprocess execution."""

    def __call__(
        self, arguments: tuple[str, ...], cwd: Path, timeout_seconds: float
    ) -> GitCommandResult:
        """Run Git with a bounded deadline and captured output."""
        ...


@dataclass(frozen=True, slots=True)
class GitCommandError(Exception):
    """A Git subprocess returned nonzero or could not be launched."""

    arguments: tuple[str, ...]
    returncode: int | None

    @override
    def __str__(self) -> str:
        return f"git command failed ({self.returncode}): {self.arguments[0]}"


@dataclass(frozen=True, slots=True)
class GitInterruptedError(Exception):
    """A bounded Git subprocess exceeded its deadline."""

    arguments: tuple[str, ...]
    cwd: Path
    timeout_seconds: float

    @override
    def __str__(self) -> str:
        return f"git command interrupted after {self.timeout_seconds:g}s"


def run_git(
    arguments: tuple[str, ...], cwd: Path, timeout_seconds: float
) -> GitCommandResult:
    """Execute Git without a shell and convert deadline expiry to a typed error."""
    if _GIT_EXECUTABLE is None:
        raise GitCommandError(arguments, None)
    try:
        completed = subprocess.run(  # noqa: S603 - executable is resolved, not input
            (_GIT_EXECUTABLE, *arguments),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise GitInterruptedError(arguments, cwd, timeout_seconds) from error
    except OSError as error:
        raise GitCommandError(arguments, None) from error
    return GitCommandResult(completed.returncode, completed.stdout, completed.stderr)
