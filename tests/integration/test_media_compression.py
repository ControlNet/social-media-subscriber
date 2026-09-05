"""Compression switches use generated local media and no provider requests."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import anyio
import pytest
from PIL import Image

from social_media_subscriber.application.collect import collect_snapshot
from social_media_subscriber.application.results import CollectionExitCode
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.media import convert
from social_media_subscriber.media.archive import archive_media
from social_media_subscriber.media.slots import media_slots
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.run_state import RunState
from social_media_subscriber.storage.snapshot import SnapshotState
from tests.integration._collection_application_support import (
    PERSON_URL,
    ApplicationClient,
    post,
    request,
    settings,
)
from tests.integration.test_media_pipeline import (
    _account,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.test_media_archive import synthetic_post

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.media_pipeline


def test_compression_setting_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_MEDIA_COMPRESSION", raising=False)
    assert settings(_account().profile_url).enable_media_compression is False
    monkeypatch.setenv("ENABLE_MEDIA_COMPRESSION", "true")
    assert settings(_account().profile_url).enable_media_compression is True
    monkeypatch.setenv("ENABLE_MEDIA_COMPRESSION", "false")
    assert settings(_account().profile_url).enable_media_compression is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("format_name", "suffix"),
    [("PNG", ".png"), ("JPEG", ".jpg"), ("GIF", ".gif"), ("WEBP", ".webp")],
)
async def test_uncompressed_images_keep_exact_bytes_and_survive_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, format_name: str, suffix: str
) -> None:
    source = tmp_path / ("synthetic" + suffix)
    Image.new("RGB", (16, 12), "red").save(source, format_name)
    original_bytes = source.read_bytes()
    calls = 0

    async def download(_url: str, target: Path, _limit: int) -> None:
        nonlocal calls
        calls += 1
        _ = await anyio.Path(target).write_bytes(original_bytes)

    def forbidden_conversion(_source: Path, _destination: Path) -> None:
        pytest.fail("Original media must not be re-encoded")

    monkeypatch.setattr(convert, "download", download)
    monkeypatch.setattr(convert, "convert_image", forbidden_conversion)
    state, failures = await archive_media(
        SnapshotState((_account(),), (synthetic_post(),), RunState()),
        tmp_path / "media-work",
        enable_compression=False,
    )
    assert failures == 0
    path = next(iter(state.media))
    assert path.suffix == suffix
    assert state.media[path].path.read_bytes() == original_bytes
    assert (
        media_slots(state.posts[0])[0].source_url == "/social-media/" + path.as_posix()
    )
    snapshot = tmp_path / "snapshot"
    _ = SnapshotRepository(snapshot).write(state)
    reloaded = SnapshotRepository(snapshot).load_optional()
    assert reloaded is not None
    again, failures = await archive_media(
        reloaded, tmp_path / "next", enable_compression=True
    )
    assert failures == 0
    assert again.posts == reloaded.posts
    assert calls == 1


@pytest.mark.anyio
async def test_uncompressed_video_never_invokes_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "synthetic.mp4"
    _ = await anyio.run_process(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x48:r=10:d=0.2",
            "-c:v",
            "libx264",
            str(source),
        ]
    )
    original_bytes = source.read_bytes()

    async def download(_url: str, target: Path, _limit: int) -> None:
        _ = await anyio.Path(target).write_bytes(original_bytes)

    async def forbidden_conversion(_source: Path, _destination: Path) -> None:
        pytest.fail("Compression disabled must never invoke FFmpeg")

    monkeypatch.setattr(convert, "download", download)
    monkeypatch.setattr(convert, "_convert_video", forbidden_conversion)
    slot = replace(media_slots(synthetic_post())[0], kind="video")
    output = await convert.materialize_media(
        slot, tmp_path / "output.webm", enable_compression=False
    )
    assert output.suffix == ".mp4"
    assert output.read_bytes() == original_bytes

    account = _account().model_copy(
        update={
            "platform": Platform.X,
            "kind": AccountKind.PROFILE,
            "profile_url": "https://x.com/synthetic/",
        }
    )
    video_post = synthetic_post().model_copy(
        update={
            "account_profile_url": account.profile_url,
            "canonical_url": "https://x.com/synthetic/status/1",
            "content": {
                "media": [
                    {
                        "type": "video",
                        "videoVariants": [
                            {
                                "url": "https://video.twimg.com/synthetic.mp4",
                                "contentType": "video/mp4",
                            }
                        ],
                    }
                ]
            },
        }
    )
    archived, failures = await archive_media(
        SnapshotState((account,), (video_post,), RunState()),
        tmp_path / "x-media",
        enable_compression=False,
    )
    assert failures == 0
    snapshot = tmp_path / "x-snapshot"
    _ = SnapshotRepository(snapshot).write(archived)
    reloaded = SnapshotRepository(snapshot).load_optional()
    assert reloaded is not None
    assert media_slots(reloaded.posts[0])[0].source_url.endswith(".mp4")
    assert "video/mp4" in str(reloaded.posts[0].content)
    monkeypatch.setattr(convert, "download", forbidden_conversion)
    reused, failures = await archive_media(
        reloaded, tmp_path / "x-next", enable_compression=True
    )
    assert failures == 0
    assert reused.posts == reloaded.posts


@pytest.mark.anyio
@pytest.mark.parametrize("configured", [None, "false"])
async def test_collection_passes_compression_environment_to_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: str | None
) -> None:
    if configured is None:
        monkeypatch.delenv("ENABLE_MEDIA_COMPRESSION", raising=False)
    else:
        monkeypatch.setenv("ENABLE_MEDIA_COMPRESSION", configured)
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (16, 12), "red").save(source)
    original_bytes = source.read_bytes()

    async def download(_url: str, target: Path, _limit: int) -> None:
        _ = await anyio.Path(target).write_bytes(original_bytes)

    monkeypatch.setattr(convert, "download", download)
    record = post("1")
    record = type(record).model_validate(
        record.payload | {"images": ["https://media.licdn.com/synthetic.png"]}
    )
    result = await collect_snapshot(
        request(tmp_path, settings(PERSON_URL)),
        lambda _: ApplicationClient(person_posts=(record,)),
    )
    assert result.exit_code == CollectionExitCode.SUCCESS
    saved = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert saved is not None
    assert len(saved.posts) == 1
    assert len(saved.media) == 1
    path, body = next(iter(saved.media.items()))
    assert path.suffix == ".png"
    assert body.path.read_bytes() == original_bytes
