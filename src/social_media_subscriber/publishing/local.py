"""Local snapshot publication into an existing bind-mounted directory."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from social_media_subscriber.storage.binary import (
    BinaryFile,
    FilePayload,
    payload_chunks,
)
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.safe_directory import FileIdentity, UnsafePathError


def atomic_file(  # noqa: C901 - retain path checks alongside the two immutable reuse cases
    root: Path, relative: Path, payload: FilePayload, *, immutable: bool = False
) -> None:
    """Finish a file on the destination filesystem before exposing its final name."""
    if relative.is_absolute() or any(part in (".", "..") for part in relative.parts):
        raise UnsafePathError
    parent = root
    for component in ("", *relative.parts[:-1]):
        parent = parent / component
        if parent.is_symlink():
            raise UnsafePathError
        parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    destination = parent / relative.name
    if destination.is_symlink():
        raise UnsafePathError
    if immutable and destination.exists():
        if isinstance(payload, BinaryFile) and payload.path == destination.absolute():
            # The local inventory already verified this regular, nonempty file.
            # Its contents are immutable; routine refresh never rehashes them.
            if payload.identity != FileIdentity.from_stat(destination.lstat()):
                raise UnsafePathError
            return
        if not isinstance(payload, BinaryFile) or BinaryFile.inspect(
            destination
        ).digest != (payload.digest or BinaryFile.inspect(payload.path).digest):
            raise UnsafePathError
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".publishing-", dir=parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            for chunk in payload_chunks(payload):
                _ = output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o644)
        if immutable:
            os.link(temporary, destination)
        else:
            _ = Path(temporary).replace(destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def publish_local(candidate: Path, destination: Path) -> None:
    """Publish complete media, records, indexes, then shared business state."""
    validated = SnapshotRepository(
        candidate, local_media_root=destination
    ).read_optional()
    if validated is None:
        raise UnsafePathError
    files = validated.files
    for relative, payload in sorted(files.items()):
        if relative.parts[0] == "media":
            atomic_file(destination, relative, payload, immutable=True)
    indexes = (Path("accounts.json"), Path("posts.json"), Path("state.json"))
    for relative, payload in sorted(files.items()):
        if relative.parts[0] != "media" and relative not in indexes:
            atomic_file(destination, relative, payload)
    for relative in indexes:
        if relative in files:
            atomic_file(destination, relative, files[relative])
    # A killed publisher may leave an unfinished temporary file. Only our reserved
    # names in the candidate's known output directories are eligible for cleanup.
    _remove_interrupted_files({destination / relative.parent for relative in files})


def _remove_interrupted_files(parents: set[Path]) -> None:
    for parent in parents:
        for temporary in parent.glob(".publishing-*"):
            if temporary.is_symlink() or not temporary.is_file():
                raise UnsafePathError
            temporary.unlink()
