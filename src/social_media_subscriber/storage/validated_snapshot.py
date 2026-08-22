"""Immutable results from one descriptor-anchored snapshot read."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from social_media_subscriber.storage.layout import MANIFEST
from social_media_subscriber.storage.safe_directory import (
    DirectoryAnchor,
    FileIdentity,
    UnsafePathError,
)
from social_media_subscriber.storage.snapshot import SnapshotManifest, SnapshotState

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from typing import Self


@dataclass(frozen=True, slots=True)
class ValidatedSnapshot:
    """Validated state and bytes safe to consume after descriptors close."""

    state: SnapshotState
    manifest: SnapshotManifest
    files: Mapping[Path, bytes]

    @classmethod
    def from_files(cls, state: SnapshotState, files: dict[Path, bytes]) -> Self:
        """Freeze one complete validated file inventory."""
        manifest = SnapshotManifest.model_validate_json(files[MANIFEST])
        return cls(state, manifest, MappingProxyType(dict(files)))


def require_entry_identity(anchor: DirectoryAnchor, expected: FileIdentity) -> None:
    """Require the anchored snapshot name to retain its validated identity."""
    if anchor.entry_identity() != expected:
        raise UnsafePathError
