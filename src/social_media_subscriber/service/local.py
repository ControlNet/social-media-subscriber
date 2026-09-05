"""One-shot local refresh with a persistent lock and publication recovery."""

from __future__ import annotations

import fcntl
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio

from social_media_subscriber.application.collect import (
    CollectionRequest,
    collect_snapshot,
)
from social_media_subscriber.application.results import (
    CollectionExitCode,
    CollectionResult,
)
from social_media_subscriber.publishing.local import publish_local
from social_media_subscriber.storage.safe_directory import UnsafePathError

if TYPE_CHECKING:
    from pathlib import Path

    from social_media_subscriber.settings import Settings


def refresh_local(
    snapshot: Path, state_dir: Path, settings: Settings
) -> CollectionResult | None:
    """Resume a complete interrupted publication before collecting again."""
    snapshot = snapshot.absolute()
    state_dir = state_dir.absolute()
    if (
        snapshot == state_dir
        or snapshot in state_dir.parents
        or state_dir in snapshot.parents
    ):
        raise UnsafePathError
    if snapshot.is_symlink() or state_dir.is_symlink():
        raise UnsafePathError
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = state_dir / "run.lock"
    if lock_path.is_symlink():
        raise UnsafePathError
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        return _refresh_locked(snapshot, state_dir, settings)


def _refresh_locked(
    snapshot: Path, state_dir: Path, settings: Settings
) -> CollectionResult:
    candidate = state_dir / "candidate"
    if candidate.exists():
        # Promoted candidates contain complete JSON and any newly archived media.
        publish_local(candidate, snapshot)
        shutil.rmtree(candidate)
    previous = snapshot
    if not snapshot.exists() or not any(snapshot.iterdir()):
        previous = state_dir / "absent-baseline"
        if previous.exists():
            raise UnsafePathError
    result = anyio.run(
        collect_snapshot,
        CollectionRequest(
            settings,
            previous,
            candidate,
            datetime.now(UTC),
            local_media_root=snapshot,
        ),
    )
    if result.exit_code in (CollectionExitCode.SUCCESS, CollectionExitCode.PARTIAL):
        publish_local(candidate, snapshot)
        shutil.rmtree(candidate)
    return result
