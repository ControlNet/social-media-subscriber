"""Atomic filesystem repository for deterministic snapshot trees."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, override

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
    read_json,
)
from social_media_subscriber.storage.layout import (
    ACCOUNTS_DIRECTORY,
    ACCOUNTS_INDEX,
    FEED_INDEX,
    MANIFEST,
    POSTS_DIRECTORY,
    SOURCE_DIRECTORY,
    snapshot_digest,
)
from social_media_subscriber.storage.snapshot import (
    AccountsIndex,
    FeedIndex,
    SnapshotManifest,
    SnapshotState,
)


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
        if not self._root.exists():
            return None
        try:
            return self._load()
        except (OSError, RuntimeError, ValidationError, shutil.Error) as error:
            raise SnapshotIntegrityError(type(error).__name__) from error

    def _load(self) -> SnapshotState:
        manifest = read_json(self._root / MANIFEST, SnapshotManifest)
        files = {
            path.relative_to(self._root): path.read_bytes()
            for path in self._root.rglob("*")
            if path.is_file() and path.relative_to(self._root) != MANIFEST
        }
        if manifest.digest != snapshot_digest(files):
            raise SnapshotIntegrityError(SnapshotIntegrityCategory.MANIFEST_DIGEST)
        accounts = tuple(
            read_json(path, Account)
            for path in sorted((self._root / ACCOUNTS_DIRECTORY).glob("*.json"))
        )
        posts = tuple(
            read_json(path, Post)
            for path in sorted((self._root / POSTS_DIRECTORY).glob("*.json"))
        )
        sources = tuple(
            read_json(path, BrightDataLinkedInPostSourceRecord)
            for path in sorted((self._root / SOURCE_DIRECTORY).glob("*.json"))
        )
        state = SnapshotState(
            tuple(sorted(accounts, key=lambda account: account.id)),
            tuple(sorted(posts, key=lambda post: post.id)),
            tuple(
                sorted(sources, key=lambda record: post_id_for(record.platform_post_id))
            ),
        )
        expected = self._files(state)
        expected[ACCOUNTS_INDEX] = self._encoder(self._accounts_index(state))
        expected[FEED_INDEX] = self._encoder(self._feed_index(state))
        if files != expected:
            raise SnapshotIntegrityError(SnapshotIntegrityCategory.INVENTORY)
        if (
            manifest.account_count,
            manifest.post_count,
            manifest.source_record_count,
        ) != (len(accounts), len(posts), len(sources)):
            raise SnapshotIntegrityError(SnapshotIntegrityCategory.MANIFEST_COUNTS)
        return state

    def write(self, state: SnapshotState) -> SnapshotManifest:
        """Build, validate, and atomically promote a complete snapshot tree."""
        self._root.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{self._root.name}.", dir=self._root.parent)
        )
        try:
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
            for relative_path, payload in files.items():
                destination = temporary / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                _ = destination.write_bytes(payload)
            _ = SnapshotRepository(temporary, self._encoder).load_optional()
            self._promote(temporary)
        except (OSError, RuntimeError, ValidationError, shutil.Error) as error:
            raise SnapshotIntegrityError(type(error).__name__) from error
        else:
            return manifest
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _promote(self, temporary: Path) -> None:
        if not self._root.exists():
            _ = temporary.replace(self._root)
            return
        backup = Path(
            tempfile.mkdtemp(
                prefix=f".{self._root.name}.previous.", dir=self._root.parent
            )
        )
        backup.rmdir()
        _ = self._root.replace(backup)
        try:
            _ = temporary.replace(self._root)
        except OSError:
            self._restore_prior_snapshot(backup)
            raise
        shutil.rmtree(backup)

    def _restore_prior_snapshot(self, backup: Path) -> None:
        try:
            _ = backup.replace(self._root)
        except OSError:
            recovery = Path(
                tempfile.mkdtemp(
                    prefix=f".{self._root.name}.previous-recovery.",
                    dir=self._root.parent,
                )
            )
            recovery.rmdir()
            try:
                _ = shutil.copytree(backup, recovery)
                _ = recovery.rename(self._root)
            except (OSError, shutil.Error):
                if recovery.exists():
                    shutil.rmtree(recovery)
                _ = backup.replace(self._root)
                raise
            shutil.rmtree(backup)

    def _files(self, state: SnapshotState) -> dict[Path, bytes]:
        files = {
            ACCOUNTS_DIRECTORY / record_filename(account.id): self._encoder(account)
            for account in state.accounts
        }
        files.update(
            {
                POSTS_DIRECTORY / record_filename(post.id): self._encoder(post)
                for post in state.posts
            }
        )
        files.update(
            {
                source_record_path(source): self._encoder(source)
                for source in state.source_records
            }
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
