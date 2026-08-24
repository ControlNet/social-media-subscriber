from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
from tests.unit.test_storage_repository import storage_state

if TYPE_CHECKING:
    from pathlib import Path


def test_repository_rejects_unknown_persisted_fields(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _ = SnapshotRepository(root).write(storage_state())
    record = next(root.glob("posts/linkedin/*.json"))
    payload = record.read_text().replace(
        '"type": "post"', '"schema_version": 2,\n  "type": "post"'
    )
    _ = record.write_text(payload)

    with pytest.raises(SnapshotIntegrityError):
        _ = SnapshotRepository(root).load_optional()


def test_repository_round_trips_safe_metrics_inside_post_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    state = storage_state()
    _ = SnapshotRepository(root).write(state)

    loaded = SnapshotRepository(root).load_optional()

    assert loaded is not None
    assert loaded.posts[0].content["num_likes"] == 1
