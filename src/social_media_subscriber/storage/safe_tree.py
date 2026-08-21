"""No-follow snapshot tree reads and writes anchored to directory descriptors."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from social_media_subscriber.storage.safe_directory import (
    FileIdentity,
    UnsafePathError,
)

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_WRITE_FLAGS: Final = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
)


@dataclass(frozen=True, slots=True)
class DirectoryTree:
    """A complete descriptor-read snapshot tree."""

    files: dict[Path, bytes]
    directories: frozenset[Path]


def read_directory_tree(descriptor: int) -> DirectoryTree:
    """Read a tree without following a symlink or reopening an absolute path."""
    files: dict[Path, bytes] = {}
    directories: set[Path] = set()
    _read_directory(descriptor, Path(), files, directories)
    return DirectoryTree(files, frozenset(directories))


def write_directory_tree(descriptor: int, files: dict[Path, bytes]) -> None:
    """Create one complete tree beneath a new private directory descriptor."""
    for relative_path, payload in sorted(
        files.items(), key=lambda item: item[0].as_posix()
    ):
        parts = _safe_parts(relative_path)
        parent = os.dup(descriptor)
        try:
            for component in parts[:-1]:
                parent = _open_or_create_directory(parent, component)
            write_file_at(parent, parts[-1], payload)
        finally:
            os.close(parent)


def write_file_at(descriptor: int, name: str, payload: bytes) -> None:
    """Create one private file for an already validated tree path."""
    file_descriptor = os.open(name, _WRITE_FLAGS, 0o600, dir_fd=descriptor)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(file_descriptor, remaining)
            if written == 0:
                message = "snapshot file write made no progress"
                raise OSError(message)
            remaining = remaining[written:]
    finally:
        os.close(file_descriptor)


def expected_directories(files: dict[Path, bytes]) -> frozenset[Path]:
    """Return every directory implied by a flat relative-path inventory."""
    directories: set[Path] = set()
    for relative_path in files:
        parent = relative_path.parent
        while parent != Path():
            directories.add(parent)
            parent = parent.parent
    return frozenset(directories)


def _read_directory(
    descriptor: int,
    prefix: Path,
    files: dict[Path, bytes],
    directories: set[Path],
) -> None:
    with os.scandir(descriptor) as entries:
        ordered = sorted(entries, key=lambda entry: entry.name)
    for entry in ordered:
        scanned = entry.stat(follow_symlinks=False)
        relative_path = prefix / entry.name
        if stat.S_ISDIR(scanned.st_mode):
            directories.add(relative_path)
            child = _open_verified(entry.name, descriptor, scanned, directory=True)
            try:
                _read_directory(child, relative_path, files, directories)
                _require_identity(child, scanned)
            finally:
                os.close(child)
        elif stat.S_ISREG(scanned.st_mode):
            files[relative_path] = _read_file(entry.name, descriptor, scanned)
        else:
            raise UnsafePathError


def _read_file(name: str, parent: int, scanned: os.stat_result) -> bytes:
    descriptor = _open_verified(name, parent, scanned, directory=False)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        _require_identity(descriptor, scanned)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _open_verified(
    name: str, parent: int, scanned: os.stat_result, *, directory: bool
) -> int:
    flags = _DIRECTORY_FLAGS if directory else _READ_FLAGS
    descriptor = os.open(name, flags, dir_fd=parent)
    try:
        _require_identity(descriptor, scanned)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _require_identity(descriptor: int, scanned: os.stat_result) -> None:
    if FileIdentity.from_stat(os.fstat(descriptor)) != FileIdentity.from_stat(scanned):
        raise UnsafePathError


def _open_or_create_directory(parent: int, name: str) -> int:
    with suppress(FileExistsError):
        os.mkdir(name, mode=0o700, dir_fd=parent)
    child = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    os.close(parent)
    return child


def _safe_parts(relative_path: Path) -> tuple[str, ...]:
    if relative_path.is_absolute():
        raise UnsafePathError
    parts = relative_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise UnsafePathError
    return parts
