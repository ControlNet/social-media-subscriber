from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
from social_media_subscriber.storage.snapshot import SnapshotState
from tests.unit.test_storage_repository import storage_state, tree_bytes

if TYPE_CHECKING:
    from social_media_subscriber.serialization.json import JsonBoundaryModel


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
        _ = failing.write(SnapshotState((), (), ()))
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
    original_write = Path.write_bytes
    writes = 0

    def interrupted_write(path: Path, payload: bytes) -> int:
        nonlocal writes
        writes += 1
        if writes == 3:
            message = "injected partial write interruption"
            raise OSError(message)
        return original_write(path, payload)

    monkeypatch.setattr(Path, "write_bytes", interrupted_write)

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = repository.write(storage_state())
    assert tree_bytes(root) == before


def test_double_promotion_interruption_restores_the_prior_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    state = storage_state()
    _ = repository.write(state)
    before = tree_bytes(root)
    original_replace = Path.replace

    def interrupted_replace(path: Path, target: Path) -> Path:
        if target == root and ".previous." not in path.name:
            message = "injected candidate promotion interruption"
            raise OSError(message)
        if target == root and ".previous." in path.name:
            message = "injected rollback promotion interruption"
            raise OSError(message)
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", interrupted_replace)

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = repository.write(SnapshotState((), (), ()))
    assert root.is_dir()
    assert tree_bytes(root) == before
    assert repository.load_optional() == state
    assert list(tmp_path.glob(".dist.*")) == []


def test_interrupted_recovery_copy_never_exposes_a_partial_live_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    state = storage_state()
    _ = repository.write(state)
    before = tree_bytes(root)
    original_replace = Path.replace
    rollback_interrupted = False

    def interrupted_replace(path: Path, target: Path) -> Path:
        nonlocal rollback_interrupted
        if target == root and ".previous." not in path.name:
            message = "injected candidate promotion interruption"
            raise OSError(message)
        if target == root and ".previous." in path.name and not rollback_interrupted:
            rollback_interrupted = True
            message = "injected rollback promotion interruption"
            raise OSError(message)
        return original_replace(path, target)

    def interrupted_copy(source: Path, destination: Path) -> Path:
        first_file = next(path for path in source.rglob("*") if path.is_file())
        copied = destination / first_file.relative_to(source)
        copied.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(first_file, copied)
        message = "injected recovery copy interruption"
        raise shutil.Error([(str(first_file), str(copied), message)])

    monkeypatch.setattr(Path, "replace", interrupted_replace)
    monkeypatch.setattr(shutil, "copytree", interrupted_copy)

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = repository.write(SnapshotState((), (), ()))
    assert root.is_dir()
    assert tree_bytes(root) == before
    assert repository.load_optional() == state
    assert list(tmp_path.glob(".dist.*")) == []
