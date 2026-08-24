from __future__ import annotations

from typing import TYPE_CHECKING, override

import pytest

from social_media_subscriber import cli_application
from social_media_subscriber.cli_application import DefaultCliApplication
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityCategory,
    SnapshotIntegrityError,
    SnapshotRepository,
)
from tests.unit.test_storage_repository import storage_state, tree_bytes

if TYPE_CHECKING:
    from pathlib import Path

    from social_media_subscriber.storage.safe_tree import DirectoryTree
    from social_media_subscriber.storage.snapshot import SnapshotState, SnapshotSummary


def test_verify_rejects_root_replacement_after_descriptor_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "dist"
    validated_root = tmp_path / "validated"
    outside = tmp_path / "outside"
    _ = SnapshotRepository(root).write(storage_state())
    prior_bytes = tree_bytes(root)
    outside.mkdir()
    sentinel = outside / "accounts.json"
    _ = sentinel.write_bytes(b"external sentinel")
    replaced = False

    class ReplacingRepository(SnapshotRepository):
        @override
        def _load_tree(
            self, tree: DirectoryTree
        ) -> tuple[SnapshotState, SnapshotSummary]:
            nonlocal replaced
            result = super()._load_tree(tree)
            if not replaced:
                replaced = True
                _ = root.rename(validated_root)
                root.symlink_to(outside, target_is_directory=True)
            return result

    monkeypatch.setattr(cli_application, "SnapshotRepository", ReplacingRepository)

    with pytest.raises(SnapshotIntegrityError) as raised:
        _ = DefaultCliApplication().verify(root)

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert root.is_symlink()
    assert sentinel.read_bytes() == b"external sentinel"
    assert tree_bytes(validated_root) == prior_bytes


def test_validated_snapshot_bytes_remain_safe_after_descriptor_close(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    moved = tmp_path / "moved"
    state = storage_state()
    summary = SnapshotRepository(root).write(state)
    expected_files = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    validated = SnapshotRepository(root).read_optional()
    _ = root.rename(moved)

    assert validated is not None
    assert validated.state == state
    assert validated.summary == summary
    assert dict(validated.files) == expected_files
