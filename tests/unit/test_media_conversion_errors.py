"""Synthetic local conversion failures distinguish media from worker problems."""

from __future__ import annotations

import errno
from dataclasses import replace
from typing import TYPE_CHECKING

import anyio
import pytest
from PIL import Image

from social_media_subscriber.media import convert
from social_media_subscriber.media.errors import MediaError
from social_media_subscriber.media.slots import media_slots
from tests.unit.test_media_archive import synthetic_post

if TYPE_CHECKING:
    from pathlib import Path


def test_invalid_image_is_an_expected_conversion_failure(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-invalid.png"
    _ = source.write_bytes(b"explicitly synthetic malformed image")
    with pytest.raises(MediaError, match="conversion"):
        convert.convert_image(source, tmp_path / "result.webp")


def test_missing_input_is_a_worker_failure(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        convert.convert_image(tmp_path / "missing.png", tmp_path / "result.webp")


def test_unwritable_output_is_a_worker_failure(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.png"
    Image.new("RGB", (16, 12)).save(source)
    with pytest.raises(IsADirectoryError):
        convert.convert_image(source, tmp_path)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        TypeError("synthetic bug"),
        ValueError("synthetic bug"),
        OSError(errno.ENOSPC, "synthetic full disk"),
    ],
)
async def test_unexpected_converter_errors_are_not_reclassified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    async def download(_url: str, target: Path, _limit: int) -> None:
        _ = await anyio.Path(target).write_bytes(b"synthetic downloaded input")

    def broken(_source: Path, _destination: Path) -> None:
        raise error

    monkeypatch.setattr(convert, "download", download)
    monkeypatch.setattr(convert, "convert_image", broken)
    with pytest.raises(type(error)) as caught:
        await convert.materialize_media(
            media_slots(synthetic_post())[0], tmp_path / "result.webp"
        )
    assert caught.value is error


@pytest.mark.anyio
async def test_video_timeout_is_an_expected_conversion_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def download(_url: str, target: Path, _limit: int) -> None:
        _ = await anyio.Path(target).write_bytes(b"synthetic video input")

    async def timeout(_source: Path, _destination: Path) -> None:
        raise TimeoutError

    monkeypatch.setattr(convert, "download", download)
    monkeypatch.setattr(convert, "_convert_video", timeout)
    slot = replace(media_slots(synthetic_post())[0], kind="video")
    with pytest.raises(MediaError, match="conversion"):
        await convert.materialize_media(slot, tmp_path / "result.webm")
