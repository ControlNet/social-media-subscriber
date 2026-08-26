"""Stable snapshot paths and tree hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Final

from social_media_subscriber.domain.platform import Platform

if TYPE_CHECKING:
    from collections.abc import Mapping

ACCOUNTS_DIRECTORY: Final = Path("accounts")
POSTS_ROOT: Final = Path("posts")
ACCOUNTS_INDEX: Final = Path("accounts.json")
POSTS_INDEX: Final = Path("posts.json")


def posts_directory(platform: Platform) -> Path:
    """Return the stable record directory for one platform."""
    return POSTS_ROOT / platform.value


POSTS_DIRECTORY: Final = posts_directory(Platform.LINKEDIN)


def snapshot_digest(files: Mapping[Path, bytes]) -> str:
    """Hash sorted relative paths and bytes with an unambiguous separator."""
    digest = hashlib.sha256()
    for relative_path in sorted(files, key=lambda path: path.as_posix()):
        digest.update(relative_path.as_posix().encode())
        digest.update(b"\0")
        digest.update(files[relative_path])
    return digest.hexdigest()
