"""Descriptor-anchored filesystem operations for snapshot paths."""

from __future__ import annotations

import errno
import os
import secrets
import shutil
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from types import TracebackType

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_UNSAFE_ERRNOS = frozenset({errno.ELOOP, errno.ENOTDIR})
_RMTREE_AVOIDS_SYMLINK_ATTACKS = shutil.rmtree.avoids_symlink_attacks


class UnsafePathError(OSError):
    """A path component or entry is not a stable real directory."""


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Stable identity for one opened filesystem object."""

    device: int
    inode: int

    @classmethod
    def from_stat(cls, result: os.stat_result) -> Self:
        """Build an identity from one stat result."""
        return cls(result.st_dev, result.st_ino)


@dataclass(slots=True)
class OpenDirectory:
    """Owned no-follow directory descriptor."""

    descriptor: int
    identity: FileIdentity

    def __enter__(self) -> Self:
        """Return this owned descriptor context."""
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the owned descriptor."""
        _ = (exception_type, exception, traceback)
        os.close(self.descriptor)


@dataclass(slots=True)
class DirectoryAnchor:
    """Anchor one named entry to a verified parent directory descriptor."""

    descriptor: int
    parent_path: Path
    parent_identity: FileIdentity
    entry_name: str

    @classmethod
    def open(cls, entry_path: Path, *, create_parent: bool) -> Self:
        """Open the owner-controlled parent of one named snapshot entry."""
        absolute = _absolute_path(entry_path)
        if not absolute.name:
            raise UnsafePathError
        descriptor = _open_directory_chain(absolute.parent, create=create_parent)
        result = os.fstat(descriptor)
        _require_private_owner_directory(result)
        return cls(
            descriptor,
            absolute.parent,
            FileIdentity.from_stat(result),
            absolute.name,
        )

    def __enter__(self) -> Self:
        """Return this owned anchor context."""
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the owned parent descriptor."""
        _ = (exception_type, exception, traceback)
        os.close(self.descriptor)

    def verify_parent_path(self) -> None:
        """Verify that the lexical parent still resolves to this descriptor."""
        descriptor = _open_directory_chain(self.parent_path, create=False)
        try:
            if FileIdentity.from_stat(os.fstat(descriptor)) != self.parent_identity:
                raise UnsafePathError
        finally:
            os.close(descriptor)

    def entry_identity(self, name: str | None = None) -> FileIdentity | None:
        """Read a no-follow directory identity relative to the parent."""
        entry = self.entry_name if name is None else name
        try:
            result = os.stat(entry, dir_fd=self.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(result.st_mode):
            raise UnsafePathError
        return FileIdentity.from_stat(result)

    def open_entry(
        self,
        name: str | None = None,
        *,
        expected: FileIdentity | None = None,
    ) -> OpenDirectory:
        """Open and optionally identity-check one child directory."""
        entry = self.entry_name if name is None else name
        try:
            descriptor = os.open(entry, _DIRECTORY_FLAGS, dir_fd=self.descriptor)
        except OSError as error:
            if error.errno in _UNSAFE_ERRNOS:
                raise UnsafePathError from error
            raise
        identity = FileIdentity.from_stat(os.fstat(descriptor))
        if expected is not None and identity != expected:
            os.close(descriptor)
            raise UnsafePathError
        return OpenDirectory(descriptor, identity)

    def make_directory(self, prefix: str) -> str:
        """Create a private random child directory and return its name."""
        _require_safe_name(prefix)
        for _ in range(100):
            name = f"{prefix}{secrets.token_hex(8)}"
            try:
                os.mkdir(name, mode=0o700, dir_fd=self.descriptor)
            except FileExistsError:
                continue
            return name
        message = "could not allocate snapshot directory"
        raise FileExistsError(message)

    def remove_empty_directory(self, name: str) -> None:
        """Remove one validated empty child directory."""
        _require_safe_name(name)
        os.rmdir(name, dir_fd=self.descriptor)

    def rename(self, source: str, target: str) -> None:
        """Rename one validated child name within the anchored parent."""
        _require_safe_name(source)
        _require_safe_name(target)
        os.rename(
            source,
            target,
            src_dir_fd=self.descriptor,
            dst_dir_fd=self.descriptor,
        )

    def remove_tree(
        self,
        name: str,
        *,
        expected: FileIdentity,
        missing_ok: bool = False,
    ) -> None:
        """Remove an identity-checked child tree without symlink traversal."""
        _require_safe_name(name)
        identity = self.entry_identity(name)
        if identity is None and missing_ok:
            return
        if identity != expected:
            raise UnsafePathError
        if not _RMTREE_AVOIDS_SYMLINK_ATTACKS:
            raise UnsafePathError
        shutil.rmtree(name, dir_fd=self.descriptor)


def _require_safe_name(name: str) -> None:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise UnsafePathError


def _require_private_owner_directory(result: os.stat_result) -> None:
    if result.st_uid != os.geteuid() or stat.S_IMODE(result.st_mode) & 0o022:
        raise UnsafePathError


def _absolute_path(path: Path) -> Path:
    absolute = path if path.is_absolute() else Path.cwd() / path
    if ".." in absolute.parts:
        raise UnsafePathError
    return absolute


def _open_directory_chain(path: Path, *, create: bool) -> int:
    absolute = _absolute_path(path)
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in absolute.parts[1:]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(component, dir_fd=descriptor)
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except OSError as error:
                if error.errno in _UNSAFE_ERRNOS:
                    raise UnsafePathError from error
                raise
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor
