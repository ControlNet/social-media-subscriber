"""Stable snapshot paths and tree hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

ACCOUNTS_DIRECTORY: Final = Path("accounts")
POSTS_DIRECTORY: Final = Path("posts/linkedin")
SOURCE_DIRECTORY: Final = Path("source/brightdata/linkedin/posts")
ACCOUNTS_INDEX: Final = Path("accounts.json")
FEED_INDEX: Final = Path("feed.json")
MANIFEST: Final = Path("snapshot.json")


def snapshot_digest(files: Mapping[Path, bytes]) -> str:
    """Hash sorted relative paths and bytes with an unambiguous separator."""
    digest = hashlib.sha256()
    for relative_path in sorted(files, key=lambda path: path.as_posix()):
        digest.update(relative_path.as_posix().encode())
        digest.update(b"\0")
        digest.update(files[relative_path])
    return digest.hexdigest()
