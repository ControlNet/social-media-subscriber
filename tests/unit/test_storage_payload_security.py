from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from social_media_subscriber.providers.brightdata.models import JsonValue
from social_media_subscriber.serialization.json import (
    canonical_json_bytes,
    canonical_json_value_bytes,
)
from social_media_subscriber.storage.layout import MANIFEST, snapshot_digest
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.snapshot import SnapshotManifest
from tests.unit.test_storage_repository import storage_state

if TYPE_CHECKING:
    from pathlib import Path

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


def _refresh_manifest_digest(root: Path) -> None:
    non_manifest = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root) != MANIFEST
    }
    manifest = SnapshotManifest.model_validate_json((root / MANIFEST).read_bytes())
    _ = (root / MANIFEST).write_bytes(
        canonical_json_bytes(
            manifest.model_copy(update={"digest": snapshot_digest(non_manifest)})
        )
    )


def test_repository_preserves_rehashed_ordinary_source_metrics(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _ = SnapshotRepository(root).write(storage_state())
    record = next(root.glob("source/brightdata/linkedin/posts/*.json"))
    source = _JSON_OBJECT.validate_json(record.read_bytes())
    payload = _JSON_OBJECT.validate_python(source["payload"])
    ordinary: dict[str, JsonValue] = {
        "request_count": 12,
        "response_rate": 0.95,
        "error_rate": 0.05,
    }
    payload.update(ordinary)
    source["payload"] = payload
    source["payload_sha256"] = hashlib.sha256(
        canonical_json_value_bytes(payload)
    ).hexdigest()
    _ = record.write_bytes(canonical_json_value_bytes(source))
    _refresh_manifest_digest(root)

    loaded = SnapshotRepository(root).load_optional()

    assert loaded is not None
    assert loaded.source_records[0].payload.items() >= ordinary.items()
