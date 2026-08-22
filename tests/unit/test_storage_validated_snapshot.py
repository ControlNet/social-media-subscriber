from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest

from social_media_subscriber import cli_application
from social_media_subscriber.cli_application import DefaultCliApplication
from social_media_subscriber.storage.layout import MANIFEST
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityCategory,
    SnapshotIntegrityError,
    SnapshotRepository,
)
from tests.unit.test_storage_repository import storage_state, tree_bytes

if TYPE_CHECKING:
    from social_media_subscriber.storage.safe_tree import DirectoryTree
    from social_media_subscriber.storage.snapshot import SnapshotState


def test_verify_rejects_root_replacement_after_descriptor_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "dist"
    validated_root = tmp_path / "validated"
    outside = tmp_path / "outside"
    _ = SnapshotRepository(root).write(storage_state())
    prior_bytes = tree_bytes(root)
    outside.mkdir()
    sentinel = outside / MANIFEST
    _ = sentinel.write_bytes(b"external sentinel")
    original_read_bytes = Path.read_bytes
    replaced = False
    external_read = False

    class ReplacingRepository(SnapshotRepository):
        @override
        def _load_tree(self, tree: DirectoryTree) -> SnapshotState:
            nonlocal replaced
            state = super()._load_tree(tree)
            if not replaced:
                replaced = True
                _ = root.rename(validated_root)
                root.symlink_to(outside, target_is_directory=True)
            return state

    def guarded_read_bytes(path: Path) -> bytes:
        nonlocal external_read
        if path == root / MANIFEST:
            external_read = True
            message = "verify reopened the replaced snapshot path"
            raise AssertionError(message)
        return original_read_bytes(path)

    monkeypatch.setattr(cli_application, "SnapshotRepository", ReplacingRepository)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    with pytest.raises(SnapshotIntegrityError) as raised:
        _ = DefaultCliApplication().verify(root)

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert external_read is False
    assert root.is_symlink()
    assert original_read_bytes(sentinel) == b"external sentinel"
    assert tree_bytes(validated_root) == prior_bytes


def test_validated_snapshot_bytes_remain_safe_after_descriptor_close(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    moved = tmp_path / "moved"
    outside = tmp_path / "outside"
    state = storage_state()
    manifest = SnapshotRepository(root).write(state)
    expected_files = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    validated = SnapshotRepository(root).read_optional()
    _ = root.rename(moved)
    outside.mkdir()
    root.symlink_to(outside, target_is_directory=True)

    assert validated is not None
    assert validated.state == state
    assert validated.manifest == manifest
    assert dict(validated.files) == expected_files
