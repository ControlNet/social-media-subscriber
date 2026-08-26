"""Atomic filesystem repository for deterministic snapshot trees."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, override

from pydantic import ValidationError

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import record_filename
from social_media_subscriber.domain.platform import Platform
from social_media_subscriber.domain.post import Post
from social_media_subscriber.domain.post_index import PostIndexEntry, PostsIndex
from social_media_subscriber.serialization.json import (
    JsonBoundaryModel,
    JsonValue,
    canonical_json_bytes,
    canonical_json_value_bytes,
)
from social_media_subscriber.storage import safe_promotion
from social_media_subscriber.storage.layout import (
    ACCOUNTS_DIRECTORY,
    ACCOUNTS_INDEX,
    POSTS_INDEX,
    posts_directory,
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
from social_media_subscriber.storage.snapshot import SnapshotState, SnapshotSummary
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

    INVENTORY = "record or index inventory"
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
        """Bind a snapshot root and deterministic boundary encoder."""
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
                    state, summary = self._load_tree(tree)
                    anchor.verify_parent_path()
                    require_entry_identity(anchor, identity)
                    return ValidatedSnapshot.from_files(state, summary, tree.files)
        except FileNotFoundError:
            return None
        except UnsafePathError as error:
            raise SnapshotIntegrityError(
                SnapshotIntegrityCategory.UNSAFE_PATH
            ) from error
        except (OSError, RuntimeError, ValidationError, shutil.Error) as error:
            raise SnapshotIntegrityError(type(error).__name__) from error

    def write(self, state: SnapshotState) -> SnapshotSummary:
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
                    files = self._files(state)
                    with anchor.open_entry(
                        temporary_name, expected=temporary_identity
                    ) as candidate:
                        write_directory_tree(candidate.descriptor, files)
                        _, summary = self._load_tree(
                            read_directory_tree(candidate.descriptor)
                        )
                    safe_promotion.promote_directory(
                        anchor, temporary_name, temporary_identity, prior_identity
                    )
                finally:
                    anchor.remove_tree(
                        temporary_name, expected=temporary_identity, missing_ok=True
                    )
        except UnsafePathError as error:
            raise SnapshotIntegrityError(
                SnapshotIntegrityCategory.UNSAFE_PATH
            ) from error
        except (OSError, RuntimeError, ValidationError, shutil.Error) as error:
            raise SnapshotIntegrityError(type(error).__name__) from error
        return summary

    def _load_tree(self, tree: DirectoryTree) -> tuple[SnapshotState, SnapshotSummary]:
        files = tree.files
        account_paths = _record_paths(files, ACCOUNTS_DIRECTORY)
        post_paths = tuple(
            path
            for platform in Platform
            for path in _record_paths(files, posts_directory(platform))
        )
        if ACCOUNTS_INDEX not in files or POSTS_INDEX not in files:
            raise SnapshotIntegrityError(SnapshotIntegrityCategory.INVENTORY)
        accounts = tuple(
            Account.model_validate_json(files[path]) for path in account_paths
        )
        posts = tuple(Post.model_validate_json(files[path]) for path in post_paths)
        state = SnapshotState(
            tuple(sorted(accounts, key=lambda account: account.id)),
            tuple(sorted(posts, key=lambda post: post.id)),
        )
        expected = self._files(state)
        if files != expected or tree.directories != expected_directories(expected):
            raise SnapshotIntegrityError(SnapshotIntegrityCategory.INVENTORY)
        return state, SnapshotSummary(
            account_count=len(accounts),
            post_count=len(posts),
            digest=snapshot_digest(files),
        )

    def _validate_existing_snapshot(
        self, anchor: DirectoryAnchor, identity: FileIdentity
    ) -> None:
        with anchor.open_entry(expected=identity) as root:
            tree = read_directory_tree(root.descriptor)
            if tree.files or tree.directories:
                _ = self._load_tree(tree)

    def _files(self, state: SnapshotState) -> dict[Path, bytes]:
        files = {
            ACCOUNTS_DIRECTORY / record_filename(account.id): self._encoder(account)
            for account in state.accounts
        }
        files.update(
            (
                posts_directory(post.platform) / record_filename(post.id),
                self._encoder(post),
            )
            for post in state.posts
        )
        index: JsonValue = {
            account.profile_url: (
                ACCOUNTS_DIRECTORY / record_filename(account.id)
            ).as_posix()
            for account in sorted(state.accounts, key=lambda item: item.id)
        }
        files[ACCOUNTS_INDEX] = canonical_json_value_bytes(index)
        posts_index = PostsIndex(
            posts=tuple(
                PostIndexEntry(
                    path=(
                        posts_directory(post.platform) / record_filename(post.id)
                    ).as_posix(),
                    account_profile_url=post.account_profile_url,
                    published_at=post.published_at,
                    platform=post.platform,
                )
                for post in sorted(
                    state.posts,
                    key=lambda item: (item.published_at, item.id),
                    reverse=True,
                )
            )
        )
        files[POSTS_INDEX] = self._encoder(posts_index)
        return files


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
