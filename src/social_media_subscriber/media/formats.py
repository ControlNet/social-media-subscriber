"""Supported original media containers and their public MIME types."""

from pathlib import Path
from typing import Literal

from PIL import Image

from social_media_subscriber.media.errors import MediaError

IMAGE_EXTENSIONS = {
    "JPEG": "jpg",
    "PNG": "png",
    "GIF": "gif",
    "WEBP": "webp",
    "AVIF": "avif",
}
VIDEO_MIME_TYPES = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime"}


def original_extension(source: Path, kind: Literal["image", "video"]) -> str:
    """Identify downloaded bytes without re-encoding or launching FFmpeg."""
    if kind == "image":
        try:
            with Image.open(source) as image:
                extension = IMAGE_EXTENSIONS.get(image.format or "")
                image.verify()
        except (
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            raise MediaError(category="mime") from None
        except OSError as error:
            if error.errno is not None:
                raise
            raise MediaError(category="mime") from None
        if extension is None:
            raise MediaError(category="mime")
        return extension
    with source.open("rb") as video:
        header = video.read(4096)
    if header[4:8] == b"ftyp":
        return "mov" if header[8:12] == b"qt  " else "mp4"
    if header.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in header:
        return "webm"
    raise MediaError(category="mime")
