from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.storage import safe_tree
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
from social_media_subscriber.storage.safe_directory import DirectoryAnchor
from social_media_subscriber.storage.snapshot import SnapshotState
from tests.unit.test_storage_repository import storage_state, tree_bytes

if TYPE_CHECKING:
    from social_media_subscriber.serialization.json import JsonBoundaryModel


def test_successful_promotion_is_deterministic_and_cleans_temporary_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    state = storage_state()
    _ = repository.write(state)
    before_bytes = tree_bytes(root)

    _ = repository.write(state)

    assert tree_bytes(root) == before_bytes
    assert repository.load_optional() == state
    assert list(tmp_path.glob(".dist.*")) == []


def test_serialization_failure_never_promotes_candidate(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "dist"
    good = SnapshotRepository(root)
    _ = good.write(storage_state())
    before = tree_bytes(root)

    def fail_encoding(model: JsonBoundaryModel) -> bytes:
        _ = model
        message = "injected serialization interruption"
        raise OSError(message)

    failing = SnapshotRepository(root, encoder=fail_encoding)

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = failing.write(storage_state())
    assert tree_bytes(root) == before
    assert good.load_optional() == storage_state()


def test_runtime_encoding_failure_is_typed_and_preserves_prior_tree(
    tmp_path: Path,
) -> None:
    # Given
    root = tmp_path / "dist"
    good = SnapshotRepository(root)
    _ = good.write(storage_state())
    before = tree_bytes(root)

    def fail_encoding(model: JsonBoundaryModel) -> bytes:
        _ = model
        message = "injected runtime serialization interruption"
        raise RuntimeError(message)

    failing = SnapshotRepository(root, encoder=fail_encoding)

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = failing.write(SnapshotState((), ()))
    assert tree_bytes(root) == before
    assert good.load_optional() == storage_state()


def test_partial_write_failure_never_promotes_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    _ = repository.write(storage_state())
    before = tree_bytes(root)
    original_write = safe_tree.write_file_at
    writes = 0

    def interrupted_write(descriptor: int, name: str, payload: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == 3:
            message = "injected partial write interruption"
            raise OSError(message)
        original_write(descriptor, name, payload)

    monkeypatch.setattr(safe_tree, "write_file_at", interrupted_write)

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = repository.write(storage_state())
    assert tree_bytes(root) == before


def test_interrupted_promotion_preserves_previous_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    prior = storage_state()
    _ = repository.write(prior)
    account_record = next((root / "accounts").glob("*.json"))
    prior_record_bytes = account_record.read_bytes()
    before_bytes = tree_bytes(root)
    original_rename = DirectoryAnchor.rename

    def interrupt_candidate_promotion(
        anchor: DirectoryAnchor, source: str, target: str
    ) -> None:
        if target == anchor.entry_name and ".previous." not in source:
            message = "injected candidate promotion interruption"
            raise OSError(message)
        original_rename(anchor, source, target)

    monkeypatch.setattr(DirectoryAnchor, "rename", interrupt_candidate_promotion)

    # When
    with pytest.raises(SnapshotIntegrityError):
        _ = repository.write(SnapshotState((), ()))
    after_bytes = tree_bytes(root)

    # Then
    assert before_bytes == after_bytes
    assert account_record.read_bytes() == prior_record_bytes
    assert repository.load_optional() == prior
    assert str(prior.accounts[0].id) == prior.accounts[0].profile_url
    assert [path.name for path in tmp_path.iterdir()] == ["dist"]


def test_backup_cleanup_failure_rolls_back_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    prior = storage_state()
    _ = repository.write(prior)
    before = tree_bytes(root)
    original_rmtree = shutil.rmtree

    def fail_backup_cleanup(path: str | Path, *, dir_fd: int | None = None) -> None:
        if ".previous." in Path(path).name:
            message = "injected backup cleanup failure"
            raise OSError(message)
        original_rmtree(path, dir_fd=dir_fd)

    monkeypatch.setattr(shutil, "rmtree", fail_backup_cleanup)

    with pytest.raises(SnapshotIntegrityError):
        _ = repository.write(SnapshotState((), ()))

    assert tree_bytes(root) == before
    assert repository.load_optional() == prior
    assert [path.name for path in tmp_path.iterdir()] == ["dist"]
