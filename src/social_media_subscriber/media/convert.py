"""Bounded provider media downloads and local WebP/WebM conversion."""

from __future__ import annotations

import ipaddress
import shutil
import socket
import time
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import anyio
import httpx2
import structlog
from anyio.to_thread import run_sync
from PIL import Image, ImageOps

from social_media_subscriber.media.errors import MediaError
from social_media_subscriber.media.ffmpeg import run_ffmpeg
from social_media_subscriber.media.formats import original_extension
from social_media_subscriber.media.workspace import require_private_path

MAX_ATTEMPTS = 3
_LOGGER = structlog.stdlib.get_logger()

if TYPE_CHECKING:
    from pathlib import Path

    from social_media_subscriber.media.slots import MediaSlot


def validate_source(url: str) -> str:
    """Allow HTTPS media origins, never embedded credentials or arbitrary ports."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise MediaError(category="source") from None
    host = parsed.hostname or ""
    allowed = host in {"pbs.twimg.com", "video.twimg.com"} or host.endswith(
        ".licdn.com"
    )
    if (
        not allowed
        or parsed.scheme != "https"
        or port not in (None, 443)
        or parsed.username
        or parsed.password
    ):
        raise MediaError(category="source")
    return host


async def _download_once(  # noqa: C901 - validate each redirect and streamed response together
    client: httpx2.AsyncClient, url: str, target: Path, limit: int
) -> None:
    for _ in range(6):
        host = validate_source(url)
        addresses = await anyio.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(str(item[4][0])).is_global for item in addresses
        ):
            raise MediaError(category="source")
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise MediaError(category="redirect")
                url = str(response.url.join(location))
                continue
            if (
                response.status_code in (408, 429)
                or response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
            ):
                msg = "retryable"
                raise httpx2.HTTPStatusError(
                    msg, request=response.request, response=response
                )
            if response.status_code != HTTPStatus.OK:
                raise MediaError(category="http")
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            if (
                not content_type.startswith(("image/", "video/"))
                and content_type != "application/octet-stream"
            ):
                raise MediaError(category="mime")
            size = 0
            next_log = time.monotonic() + 30
            with target.open("wb") as output:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise MediaError(category="size")
                    _ = output.write(chunk)
                    if time.monotonic() >= next_log:
                        await _LOGGER.ainfo(
                            "media.download.progress", bytes_received=size
                        )
                        next_log = time.monotonic() + 30
            if not size:
                raise MediaError(category="empty")
            return
    raise MediaError(category="redirect")


async def download(url: str, target: Path, limit: int) -> None:
    """Retry temporary transport errors at most three times in one run."""
    async with httpx2.AsyncClient(
        timeout=30, follow_redirects=False, trust_env=False
    ) as client:
        for attempt in range(MAX_ATTEMPTS):
            try:
                with anyio.fail_after(600):
                    await _download_once(client, url, target, limit)
            except (
                httpx2.TransportError,
                httpx2.HTTPStatusError,
                TimeoutError,
                socket.gaierror,
            ) as error:
                if attempt == MAX_ATTEMPTS - 1:
                    raise MediaError(category="download") from None
                delay = (1.0, 2.0, 4.0)[attempt]
                if isinstance(error, httpx2.HTTPStatusError):
                    retry_after = error.response.headers.get("retry-after", "")
                    if retry_after.isdigit():
                        delay = min(max(delay, float(retry_after)), 60)
                await _LOGGER.awarning(
                    "media.download.retry",
                    attempt=attempt + 1,
                    next_attempt=attempt + 2,
                    delay_seconds=delay,
                    error_type=type(error).__name__,
                )
                await anyio.sleep(delay)
            else:
                return


def convert_image(source: Path, destination: Path) -> None:
    """Keep dimensions, orientation, transparency, and animated image frames."""
    try:
        with Image.open(source) as image:
            if getattr(image, "is_animated", False):
                image.save(destination, format="WEBP", quality=82, save_all=True)
            else:
                oriented = ImageOps.exif_transpose(image)
                converted = oriented.convert(
                    "RGBA"
                    if "A" in oriented.getbands() or "transparency" in oriented.info
                    else "RGB"
                )
                converted.save(destination, format="WEBP", quality=82)
        with Image.open(destination) as verified:
            verified.verify()
    except (ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise MediaError(category="conversion") from None
    except OSError as error:
        # Pillow decoder errors have no errno; disk/permission failures belong
        # to the worker, not this media's permanent-failure budget.
        if error.errno is not None:
            raise
        raise MediaError(category="conversion") from None


async def materialize_media(
    slot: MediaSlot, destination: Path, *, enable_compression: bool = True
) -> Path:
    """Download privately and expose an output only after successful validation."""
    source = destination.with_suffix(".input")
    partial = destination.with_suffix(".download")
    for path in (source, partial, destination):
        require_private_path(path)
    if not source.exists():
        started = time.monotonic()
        await _LOGGER.ainfo("media.download.started")
        limit = 50 * 1024 * 1024 if slot.kind == "image" else 1024 * 1024 * 1024
        await download(slot.source_url, partial, limit)
        _ = partial.replace(source)
        await _LOGGER.ainfo(
            "media.download.completed",
            bytes_received=(await anyio.Path(source).stat()).st_size,
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
    else:
        await _LOGGER.ainfo(
            "media.download.reused",
            bytes_received=(await anyio.Path(source).stat()).st_size,
        )
    try:
        if not enable_compression:
            extension = await run_sync(original_extension, source, slot.kind)
            destination = destination.with_suffix("." + extension)
            require_private_path(destination)
            _ = await run_sync(shutil.copyfile, source, destination)
            return destination
        if slot.kind == "image":
            await run_sync(convert_image, source, destination)
            return destination
        await _convert_video(source, destination)
    except MediaError:
        # Bad source bytes must not prevent the next run trying a refreshed URL.
        source.unlink(missing_ok=True)
        raise
    else:
        return destination


async def _convert_video(source: Path, destination: Path) -> None:
    """Encode and decode-check a video with local-only FFmpeg inputs."""
    await run_ffmpeg(
        [
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            "mov,matroska,webm",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-map_metadata",
            "-1",
            "-c:v",
            "libvpx-vp9",
            "-crf",
            "32",
            "-b:v",
            "0",
            "-cpu-used",
            "4",
            "-c:a",
            "libopus",
            "-b:a",
            "96k",
            "-f",
            "webm",
            "-y",
            str(destination),
        ],
        phase="encode",
    )
    await run_ffmpeg(
        [
            "-xerror",
            "-i",
            str(destination),
            "-f",
            "null",
            "-",
        ],
        phase="validate",
    )
    if not (await anyio.Path(destination).stat()).st_size:
        raise MediaError(category="conversion")
