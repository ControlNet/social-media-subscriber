from __future__ import annotations

import os
from typing import TYPE_CHECKING, override

import pytest

from social_media_subscriber.storage import safe_promotion
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityCategory,
    SnapshotIntegrityError,
    SnapshotRepository,
)
from social_media_subscriber.storage.safe_directory import (
    DirectoryAnchor,
    FileIdentity,
)
from social_media_subscriber.storage.snapshot import SnapshotState
from tests.unit.test_storage_repository import storage_state, tree_bytes

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class _PostValidationReplacingRepository(SnapshotRepository):
    _replacement: Callable[[], None]

    def __init__(self, root: Path, replacement: Callable[[], None]) -> None:
        super().__init__(root)
        self._replacement = replacement

    @override
    def _validate_existing_snapshot(
        self, anchor: DirectoryAnchor, identity: FileIdentity
    ) -> None:
        super()._validate_existing_snapshot(anchor, identity)
        self._replacement()


def test_group_writable_snapshot_parent_is_rejected_before_mutation(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o700)
    parent.chmod(0o770)
    root = parent / "dist"

    try:
        with pytest.raises(SnapshotIntegrityError) as raised:
            _ = SnapshotRepository(root).write(storage_state())
    finally:
        parent.chmod(0o700)

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert root.exists() is False
    assert list(parent.iterdir()) == []


def test_root_symlink_replacement_after_validation_is_not_followed_or_removed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    saved = tmp_path / "validated-prior"
    outside = tmp_path / "outside"
    _ = SnapshotRepository(root).write(storage_state())
    prior_bytes = tree_bytes(root)
    outside.mkdir()
    marker = outside / "marker.txt"
    _ = marker.write_bytes(b"outside")

    def replace_after_validation() -> None:
        _ = root.rename(saved)
        root.symlink_to(outside, target_is_directory=True)

    repository = _PostValidationReplacingRepository(root, replace_after_validation)
    with pytest.raises(SnapshotIntegrityError) as raised:
        _ = repository.write(SnapshotState((), ()))

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert root.is_symlink()
    assert root.readlink() == outside
    assert marker.read_bytes() == b"outside"
    assert tree_bytes(saved) == prior_bytes
    assert list(tmp_path.glob(".dist.*")) == []


def test_root_directory_replacement_after_validation_is_not_overwritten(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    saved = tmp_path / "validated-prior"
    _ = SnapshotRepository(root).write(storage_state())
    prior_bytes = tree_bytes(root)

    def replace_after_validation() -> None:
        _ = root.rename(saved)
        root.mkdir()
        _ = (root / "marker.txt").write_bytes(b"replacement")

    repository = _PostValidationReplacingRepository(root, replace_after_validation)
    with pytest.raises(SnapshotIntegrityError) as raised:
        _ = repository.write(SnapshotState((), ()))

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert tree_bytes(root) == {"marker.txt": b"replacement"}
    assert tree_bytes(saved) == prior_bytes
    assert list(tmp_path.glob(".dist.*")) == []


def test_candidate_replacement_before_promotion_is_not_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    _ = repository.write(storage_state())
    before = tree_bytes(root)
    original = safe_promotion.promote_directory
    replacement_name = ""
    captured_name = ""

    def replace_candidate(
        anchor: DirectoryAnchor,
        candidate: str,
        candidate_identity: FileIdentity,
        prior_identity: FileIdentity | None,
    ) -> None:
        nonlocal replacement_name, captured_name
        replacement_name = candidate
        captured_name = f"{candidate}.captured"
        anchor.rename(candidate, captured_name)
        os.mkdir(candidate, mode=0o700, dir_fd=anchor.descriptor)
        _write_marker(anchor.descriptor, candidate, b"replacement")
        original(anchor, candidate, candidate_identity, prior_identity)

    monkeypatch.setattr(safe_promotion, "promote_directory", replace_candidate)

    with pytest.raises(SnapshotIntegrityError) as raised:
        _ = repository.write(SnapshotState((), ()))

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert tree_bytes(root) == before
    assert (tmp_path / replacement_name / "marker.txt").read_bytes() == b"replacement"
    assert (tmp_path / captured_name).is_dir()


def test_root_occupant_after_backup_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    _ = repository.write(storage_state())
    prior_bytes = tree_bytes(root)
    original = DirectoryAnchor.rename

    def occupy_root_after_backup(
        anchor: DirectoryAnchor, source: str, target: str
    ) -> None:
        original(anchor, source, target)
        if source == anchor.entry_name and ".previous." in target:
            os.mkdir(anchor.entry_name, mode=0o700, dir_fd=anchor.descriptor)
            _write_marker(anchor.descriptor, anchor.entry_name, b"replacement")

    monkeypatch.setattr(DirectoryAnchor, "rename", occupy_root_after_backup)

    with pytest.raises(SnapshotIntegrityError) as raised:
        _ = repository.write(SnapshotState((), ()))

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert tree_bytes(root) == {"marker.txt": b"replacement"}
    backups = list(tmp_path.glob(".dist.previous.*"))
    assert len(backups) == 1
    assert tree_bytes(backups[0]) == prior_bytes


def test_nested_directory_replacement_during_load_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "dist"
    _ = SnapshotRepository(root).write(storage_state())
    replacement_started = False
    read_after_replacement = False
    original_read = os.read
    original_open = os.open

    def replace_before_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replacement_started
        if path == "accounts" and flags & os.O_DIRECTORY and not replacement_started:
            replacement_started = True
            parent = _required_descriptor(dir_fd)
            os.rename(
                "accounts",
                "accounts-original",
                src_dir_fd=parent,
                dst_dir_fd=parent,
            )
            os.mkdir("accounts", mode=0o700, dir_fd=parent)
            _write_marker(parent, "accounts", b"replacement")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def guarded_read(descriptor: int, length: int) -> bytes:
        nonlocal read_after_replacement
        read_after_replacement |= replacement_started
        return original_read(descriptor, length)

    monkeypatch.setattr(os, "open", replace_before_open)
    monkeypatch.setattr(os, "read", guarded_read)

    with pytest.raises(SnapshotIntegrityError) as raised:
        _ = SnapshotRepository(root).load_optional()

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert read_after_replacement is False
    assert (root / "accounts" / "marker.txt").read_bytes() == b"replacement"


def _required_descriptor(descriptor: int | None) -> int:
    if descriptor is None:
        raise AssertionError
    return descriptor


def _write_marker(parent: int, directory: str, payload: bytes) -> None:
    directory_descriptor = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=parent,
    )
    try:
        descriptor = os.open(
            "marker.txt",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_descriptor,
        )
        try:
            _ = os.write(descriptor, payload)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)
