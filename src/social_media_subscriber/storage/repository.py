"""Atomic filesystem repository for deterministic snapshot trees."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, override

from pydantic import ValidationError

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import post_id_for, record_filename
from social_media_subscriber.domain.post import Post
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
    source_record_path,
)
from social_media_subscriber.serialization.json import (
    JsonBoundaryModel,
    canonical_json_bytes,
)
from social_media_subscriber.storage import safe_promotion
from social_media_subscriber.storage.layout import (
    ACCOUNTS_DIRECTORY,
    ACCOUNTS_INDEX,
    FEED_INDEX,
    MANIFEST,
    POSTS_DIRECTORY,
    SOURCE_DIRECTORY,
    snapshot_digest,
)
from social_media_subscriber.storage.safe_directory import (
    DirectoryAnchor,
    FileIdentity,
    UnsafePathError,
)
from social_media_subscriber.storage.safe_tree import (
    DirectoryTree,
    expected_directories,
    read_directory_tree,
    write_directory_tree,
)
from social_media_subscriber.storage.snapshot import (
    AccountsIndex,
    FeedIndex,
    SnapshotManifest,
    SnapshotState,
)
from social_media_subscriber.storage.validated_snapshot import (
    ValidatedSnapshot,
    require_entry_identity,
)

if TYPE_CHECKING:
    from pathlib import Path


class SnapshotEncoder(Protocol):
    """Canonical serialization capability injected for failure testing."""

    def __call__(self, model: JsonBoundaryModel) -> bytes:
        """Encode one validated boundary model deterministically."""
        ...


class SnapshotIntegrityCategory(StrEnum):
    """Closed snapshot validation and materialization failures."""

    MANIFEST_DIGEST = "manifest digest"
    INVENTORY = "record or index inventory"
    MANIFEST_COUNTS = "manifest counts"
    UNSAFE_PATH = "unsafe filesystem path"


@dataclass(frozen=True, slots=True)
class SnapshotIntegrityError(Exception):
    """A snapshot is corrupt or a complete candidate could not be built."""

    reason: str | SnapshotIntegrityCategory

    @override
    def __str__(self) -> str:
        return f"snapshot integrity failure: {self.reason}"


class SnapshotRepository:
    """Validate, materialize, and atomically promote one snapshot root."""

    _root: Path
    _encoder: SnapshotEncoder

    def __init__(
        self, root: Path, encoder: SnapshotEncoder = canonical_json_bytes
    ) -> None:
        """Bind a snapshot root and deterministic encoder."""
        self._root = root
        self._encoder = encoder

    def load_optional(self) -> SnapshotState | None:
        """Load a fully validated snapshot or return None when absent."""
        validated = self.read_optional()
        return None if validated is None else validated.state

    def read_optional(self) -> ValidatedSnapshot | None:
        """Read one validated immutable tree through anchored descriptors."""
        try:
            with DirectoryAnchor.open(self._root, create_parent=False) as anchor:
                identity = anchor.entry_identity()
                if identity is None:
                    return None
                anchor.verify_parent_path()
                with anchor.open_entry(expected=identity) as root:
                    tree = read_directory_tree(root.descriptor)
                    state = self._load_tree(tree)
                    anchor.verify_parent_path()
                    require_entry_identity(anchor, identity)
                    return ValidatedSnapshot.from_files(state, tree.files)
        except FileNotFoundError:
            return None
        except UnsafePathError as error:
            raise SnapshotIntegrityError(
                SnapshotIntegrityCategory.UNSAFE_PATH
            ) from error
        except (OSError, RuntimeError, ValidationError, shutil.Error) as error:
            raise SnapshotIntegrityError(type(error).__name__) from error

    def write(self, state: SnapshotState) -> SnapshotManifest:
        """Build, validate, and atomically promote a complete snapshot tree."""
        try:
            with DirectoryAnchor.open(self._root, create_parent=True) as anchor:
                prior_identity = anchor.entry_identity()
                if prior_identity is not None:
                    self._validate_existing_snapshot(anchor, prior_identity)
                anchor.verify_parent_path()
                temporary_name = anchor.make_directory(f".{anchor.entry_name}.")
                temporary_identity = _required_identity(
                    anchor.entry_identity(temporary_name)
                )
                try:
                    files, manifest = self._candidate_files(state)
                    with anchor.open_entry(
                        temporary_name, expected=temporary_identity
                    ) as candidate:
                        write_directory_tree(candidate.descriptor, files)
                        _ = self._load_tree(read_directory_tree(candidate.descriptor))
                    safe_promotion.promote_directory(
                        anchor,
                        temporary_name,
                        temporary_identity,
                        prior_identity,
                    )
                finally:
                    anchor.remove_tree(
                        temporary_name,
                        expected=temporary_identity,
                        missing_ok=True,
                    )
        except UnsafePathError as error:
            raise SnapshotIntegrityError(
                SnapshotIntegrityCategory.UNSAFE_PATH
            ) from error
        except (OSError, RuntimeError, ValidationError, shutil.Error) as error:
            raise SnapshotIntegrityError(type(error).__name__) from error
        return manifest

    def _load_tree(self, tree: DirectoryTree) -> SnapshotState:
        manifest_payload = tree.files.get(MANIFEST)
        if manifest_payload is None:
            raise SnapshotIntegrityError(SnapshotIntegrityCategory.INVENTORY)
        manifest = SnapshotManifest.model_validate_json(manifest_payload)
        files = {
            path: payload for path, payload in tree.files.items() if path != MANIFEST
        }
        if manifest.digest != snapshot_digest(files):
            raise SnapshotIntegrityError(SnapshotIntegrityCategory.MANIFEST_DIGEST)
        accounts = tuple(
            Account.model_validate_json(files[path])
            for path in _record_paths(files, ACCOUNTS_DIRECTORY)
        )
        posts = tuple(
            Post.model_validate_json(files[path])
            for path in _record_paths(files, POSTS_DIRECTORY)
        )
        sources = tuple(
            BrightDataLinkedInPostSourceRecord.model_validate_json(files[path])
            for path in _record_paths(files, SOURCE_DIRECTORY)
        )
        state = SnapshotState(
            tuple(sorted(accounts, key=lambda account: account.id)),
            tuple(sorted(posts, key=lambda post: post.id)),
            tuple(sorted(sources, key=lambda item: post_id_for(item.platform_post_id))),
        )
        expected = self._files(state)
        expected[ACCOUNTS_INDEX] = self._encoder(self._accounts_index(state))
        expected[FEED_INDEX] = self._encoder(self._feed_index(state))
        complete_expected = {**expected, MANIFEST: manifest_payload}
        if files != expected or tree.directories != expected_directories(
            complete_expected
        ):
            raise SnapshotIntegrityError(SnapshotIntegrityCategory.INVENTORY)
        if (
            manifest.account_count,
            manifest.post_count,
            manifest.source_record_count,
        ) != (len(accounts), len(posts), len(sources)):
            raise SnapshotIntegrityError(SnapshotIntegrityCategory.MANIFEST_COUNTS)
        return state

    def _validate_existing_snapshot(
        self, anchor: DirectoryAnchor, identity: FileIdentity
    ) -> None:
        with anchor.open_entry(expected=identity) as root:
            tree = read_directory_tree(root.descriptor)
            if tree.files or tree.directories:
                _ = self._load_tree(tree)

    def _candidate_files(
        self, state: SnapshotState
    ) -> tuple[dict[Path, bytes], SnapshotManifest]:
        files = self._files(state)
        files[ACCOUNTS_INDEX] = self._encoder(self._accounts_index(state))
        files[FEED_INDEX] = self._encoder(self._feed_index(state))
        manifest = SnapshotManifest(
            account_count=len(state.accounts),
            post_count=len(state.posts),
            source_record_count=len(state.source_records),
            digest=snapshot_digest(files),
        )
        files[MANIFEST] = self._encoder(manifest)
        return files, manifest

    def _files(self, state: SnapshotState) -> dict[Path, bytes]:
        files = {
            ACCOUNTS_DIRECTORY / record_filename(account.id): self._encoder(account)
            for account in state.accounts
        }
        files.update(
            (POSTS_DIRECTORY / record_filename(post.id), self._encoder(post))
            for post in state.posts
        )
        files.update(
            (source_record_path(source), self._encoder(source))
            for source in state.source_records
        )
        return files

    @staticmethod
    def _accounts_index(state: SnapshotState) -> AccountsIndex:
        return AccountsIndex(
            accounts={
                account.id: (
                    ACCOUNTS_DIRECTORY / record_filename(account.id)
                ).as_posix()
                for account in sorted(state.accounts, key=lambda item: item.id)
            }
        )

    @staticmethod
    def _feed_index(state: SnapshotState) -> FeedIndex:
        ordered = sorted(
            state.posts, key=lambda post: (-post.published_at.timestamp(), post.id)
        )
        return FeedIndex(posts=tuple(post.id for post in ordered))


def _record_paths(files: dict[Path, bytes], directory: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in files
            if path.parent == directory and path.suffix == ".json"
        )
    )


def _required_identity(identity: FileIdentity | None) -> FileIdentity:
    if identity is None:
        raise UnsafePathError
    return identity
