"""Synthetic media safety, slot identity, and provider-independent persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.media.convert import validate_source
from social_media_subscriber.media.slots import media_slots, normalize_content
from social_media_subscriber.platforms.linkedin import canonical_media_items
from social_media_subscriber.storage.binary import BinaryFile
from social_media_subscriber.storage.safe_directory import UnsafePathError
from tests.unit.test_media_archive import synthetic_post

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "url",
    [
        "http://pbs.twimg.com/image",
        "https://pbs.twimg.com.evil.invalid/image",
        "https://localhost/image",
        "https://pbs.twimg.com:444/image",
        "https://user:password@media.licdn.com/image",
        "file:///etc/passwd",
    ],
)
def test_media_source_rejects_unapproved_origins(url: str) -> None:
    with pytest.raises(ValueError, match="source"):
        _ = validate_source(url)


def test_binary_file_detects_in_place_mutation(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.webp"
    _ = path.write_bytes(b"synthetic first content")
    payload = BinaryFile.inspect(path)
    _ = path.write_bytes(b"synthetic other content")
    with pytest.raises(UnsafePathError):
        _ = list(payload.chunks())


def test_binary_file_rejects_symlink_replacement(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.webp"
    _ = path.write_bytes(b"synthetic content")
    payload = BinaryFile.inspect(path)
    replacement = tmp_path / "replacement.webp"
    _ = path.rename(replacement)
    path.symlink_to(replacement)
    with pytest.raises(OSError, match="Too many levels"):
        _ = list(payload.chunks())


def test_linkedin_slots_keep_original_index_and_cover_renderer_fields() -> None:
    post = synthetic_post().model_copy(
        update={
            "content": {
                "images": canonical_media_items(
                    [None, "https://media.licdn.com/image"]
                ),
                "videos": [
                    {
                        "videoUrl": "https://media.licdn.com/video",
                        "thumbnailUrl": "https://media.licdn.com/poster",
                    }
                ],
                "user_profile_pic": "https://media.licdn.com/avatar",
                "document_cover_image": "https://media.licdn.com/document",
                "repost": {
                    "postImages": ["https://media.licdn.com/repost"],
                    "postVideo": {"url": "https://media.licdn.com/repost-video"},
                    "author": {
                        "avatar": {"url": "https://media.licdn.com/repost-avatar"}
                    },
                    "job": {"logoUrl": "https://media.licdn.com/logo"},
                },
            }
        }
    )
    slots = media_slots(normalize_content(post))
    identities = {(slot.scope, slot.index) for slot in slots}
    assert identities == {
        ("main-images", 1),
        ("main-videos", 0),
        ("main-video-posters", 0),
        ("author-avatars", 0),
        ("document-covers", 0),
        ("repost-images", 0),
        ("repost-videos", 0),
        ("repost-author-avatars", 0),
        ("job-logos", 0),
    }
