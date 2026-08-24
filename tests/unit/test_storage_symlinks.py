from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.serialization.json import canonical_json_bytes
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityCategory,
    SnapshotIntegrityError,
    SnapshotRepository,
)
from tests.unit.test_storage_repository import storage_state, tree_bytes

if TYPE_CHECKING:
    from social_media_subscriber.serialization.json import JsonBoundaryModel


def test_write_rejects_symlink_output_root_before_mutation(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    state = storage_state()
    _ = SnapshotRepository(target).write(state)
    before = tree_bytes(target)
    root = tmp_path / "dist"
    root.symlink_to(target, target_is_directory=True)
    encoder_calls = 0

    def recording_encoder(model: JsonBoundaryModel) -> bytes:
        nonlocal encoder_calls
        encoder_calls += 1
        return canonical_json_bytes(model)

    with pytest.raises(SnapshotIntegrityError) as raised:
        _ = SnapshotRepository(root, recording_encoder).write(state)

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert encoder_calls == 0
    assert root.is_symlink()
    assert root.readlink() == target
    assert tree_bytes(target) == before
    assert list(tmp_path.glob(".dist.*")) == []


@pytest.mark.parametrize(
    "snapshot_path",
    [
        "accounts.json",
        "accounts/*.json",
        "posts/linkedin/*.json",
    ],
    ids=("accounts-index", "account", "post"),
)
def test_load_rejects_snapshot_file_symlinks_without_following_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_path: str,
) -> None:
    root = tmp_path / "dist"
    state = storage_state()
    _ = SnapshotRepository(root).write(state)
    snapshot_file = next(root.glob(snapshot_path))
    external_file = tmp_path / "external" / snapshot_file.name
    external_file.parent.mkdir()
    external_bytes = snapshot_file.read_bytes()
    _ = external_file.write_bytes(external_bytes)
    snapshot_file.unlink()
    snapshot_file.symlink_to(external_file)
    original_read_bytes = Path.read_bytes
    followed_symlink = False

    def guarded_read_bytes(path: Path) -> bytes:
        nonlocal followed_symlink
        if path.is_symlink():
            followed_symlink = True
            message = "snapshot symlink must not be followed"
            raise AssertionError(message)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    with pytest.raises(SnapshotIntegrityError) as raised:
        _ = SnapshotRepository(root).load_optional()

    assert raised.value.reason is SnapshotIntegrityCategory.UNSAFE_PATH
    assert followed_symlink is False
    assert original_read_bytes(external_file) == external_bytes
