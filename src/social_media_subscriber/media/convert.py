"""Bounded provider media downloads and local WebP/WebM conversion."""

from __future__ import annotations

import ipaddress
import socket
import tempfile
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import anyio
import httpx2
from anyio.to_thread import run_sync
from PIL import Image, ImageOps

from social_media_subscriber.media.errors import MediaError

MAX_ATTEMPTS = 3

if TYPE_CHECKING:
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
            with target.open("wb") as output:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise MediaError(category="size")
                    _ = output.write(chunk)
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


async def materialize_media(slot: MediaSlot, destination: Path) -> None:
    """Download privately and expose an output only after successful validation."""
    with tempfile.TemporaryDirectory(prefix="media-input-") as temporary:
        source = Path(temporary) / "input"
        limit = 50 * 1024 * 1024 if slot.kind == "image" else 1024 * 1024 * 1024
        await download(slot.source_url, source, limit)
        if slot.kind == "image":
            await run_sync(convert_image, source, destination)
            return
        try:
            with anyio.fail_after(3600):
                await _convert_video(source, destination)
        except TimeoutError:
            raise MediaError(category="conversion") from None


async def _convert_video(source: Path, destination: Path) -> None:
    """Encode and decode-check a video with local-only FFmpeg inputs."""
    result = await anyio.run_process(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
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
            "-threads",
            "2",
            "-c:a",
            "libopus",
            "-b:a",
            "96k",
            "-f",
            "webm",
            "-y",
            str(destination),
        ],
        check=False,
    )
    if result.returncode:
        raise MediaError(category="conversion")
    verified = await anyio.run_process(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(destination),
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    if verified.returncode or not (await anyio.Path(destination).stat()).st_size:
        raise MediaError(category="conversion")
