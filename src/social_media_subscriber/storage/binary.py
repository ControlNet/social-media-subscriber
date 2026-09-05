"""Bounded binary reads with stable file identity and no symlink traversal."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from social_media_subscriber.storage.safe_directory import FileIdentity, UnsafePathError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class BinaryFile:
    """Reference immutable media without retaining its bytes in memory."""

    path: Path = field(compare=False)
    identity: FileIdentity = field(compare=False)
    digest: str

    @classmethod
    def inspect(cls, path: Path) -> BinaryFile:
        """Read a regular file once using bounded chunks."""
        identity = FileIdentity.from_stat(path.lstat())
        provisional = cls(path.absolute(), identity, "")
        digest = hashlib.sha256()
        for chunk in provisional.chunks():
            digest.update(chunk)
        return cls(provisional.path, identity, digest.hexdigest())

    def chunks(self) -> Iterator[bytes]:
        """Reject replaced files or symlinks in any path component."""
        parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        descriptor: int | None = None
        try:
            for part in self.path.parts[1:-1]:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
                os.close(parent)
                parent = child
            descriptor = os.open(
                self.path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or FileIdentity.from_stat(before) != self.identity
            ):
                raise UnsafePathError
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
                yield chunk
            after = os.fstat(descriptor)
            if (
                FileIdentity.from_stat(after) != self.identity
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (self.digest and digest.hexdigest() != self.digest)
            ):
                raise UnsafePathError
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)


type FilePayload = bytes | BinaryFile


def payload_chunks(payload: FilePayload) -> Iterator[bytes]:
    """Iterate JSON bytes or a bounded binary source."""
    if isinstance(payload, bytes):
        yield payload
    else:
        yield from payload.chunks()
