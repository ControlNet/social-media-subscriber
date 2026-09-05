"""Private per-slot work paths and recovery of atomically completed outputs."""

from pathlib import Path
from typing import Literal

from social_media_subscriber.media.formats import IMAGE_EXTENSIONS, VIDEO_MIME_TYPES
from social_media_subscriber.storage.binary import BinaryFile
from social_media_subscriber.storage.safe_directory import UnsafePathError


def require_private_path(path: Path) -> None:
    """Do not follow symlinks when reopening persistent worker files."""
    if any(item.is_symlink() for item in (path, *path.absolute().parents)):
        raise UnsafePathError


def completed_media(
    directory: Path, relative: Path, kind: Literal["image", "video"]
) -> tuple[Path, BinaryFile] | None:
    """Only final slot names, never partial encoding files, count as reusable."""
    extensions = IMAGE_EXTENSIONS.values() if kind == "image" else VIDEO_MIME_TYPES
    found: tuple[Path, BinaryFile] | None = None
    for extension in extensions:
        candidate = relative.with_suffix("." + extension)
        path = directory / candidate
        require_private_path(path)
        if not path.exists():
            continue
        if found is not None or not path.is_file() or not path.stat().st_size:
            raise UnsafePathError
        found = candidate, BinaryFile.inspect(path)
    return found
