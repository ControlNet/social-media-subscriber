"""Best-effort media archival and persistent retry queue transitions."""

from __future__ import annotations

import copy
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from social_media_subscriber.media.convert import materialize_media
from social_media_subscriber.media.errors import MediaError
from social_media_subscriber.media.slots import (
    PREFIX,
    MediaSlot,
    media_slots,
    normalize_content,
    set_archived,
    write_at,
)
from social_media_subscriber.media.workspace import (
    completed_media,
    require_private_path,
)
from social_media_subscriber.storage.binary import BinaryFile
from social_media_subscriber.storage.run_state import MediaFailure, RunState

if TYPE_CHECKING:
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.storage.snapshot import SnapshotState

type Materializer = Callable[[MediaSlot, Path], Awaitable[Path | None]]
_LOGGER = structlog.stdlib.get_logger()
MAX_FAILED_RUNS = 3


def _require_output(path: Path) -> None:
    if not path.is_file() or not path.stat().st_size:
        raise MediaError(category="conversion")


async def archive_media(  # noqa: PLR0915 - keep slot publication and retry transitions together
    state: SnapshotState,
    directory: Path,
    *,
    materialize: Materializer | None = None,
    enable_compression: bool = True,
    refreshed_post_ids: frozenset[str] = frozenset(),  # pyright: ignore[reportCallInDefaultInitializer]
) -> tuple[SnapshotState, int]:
    """Archive unseen slots; retain every post and source URL on failure."""
    operation = materialize or (
        materialize_media
        if enable_compression
        else partial(materialize_media, enable_compression=False)
    )
    progress = state.run_state or RunState()
    pending = {
        (item.post_id, item.scope, item.index): item for item in progress.pending_media
    }
    failed = {
        (item.post_id, item.scope, item.index): item for item in progress.failed_media
    }
    media = dict(state.media)
    archived_paths = {path.with_suffix(""): path for path in media}
    posts: list[Post] = []
    failures = 0
    normalized = tuple(normalize_content(post) for post in state.posts)
    total = sum(len(media_slots(post)) for post in normalized)
    position = 0
    await _LOGGER.ainfo(
        "media.archive.batch_started",
        posts=len(normalized),
        media=total,
        compression=enable_compression,
    )
    for post in normalized:
        content = copy.deepcopy(post.content)
        for slot in media_slots(post):
            position += 1
            key = slot.key(post)
            relative = slot.path(post)
            destination = directory / relative
            archived = archived_paths.get(relative.with_suffix(""))
            if archived is not None:
                set_archived(content, slot, PREFIX + archived.as_posix())
                _ = pending.pop(key, None)
                _ = failed.pop(key, None)
                continue
            recovered = completed_media(directory, relative, slot.kind)
            if recovered is not None:
                relative, payload = recovered
                media[relative] = payload
                archived_paths[relative.with_suffix("")] = relative
                set_archived(content, slot, PREFIX + relative.as_posix())
                _ = pending.pop(key, None)
                _ = failed.pop(key, None)
                await _LOGGER.ainfo(
                    "media.archive.reused",
                    post_id=str(post.id),
                    scope=slot.scope,
                    index=slot.index,
                )
                continue
            if key in failed:
                continue
            previous = pending.get(key)
            source = slot
            if previous is not None and str(post.id) not in refreshed_post_ids:
                source = replace(slot, source_url=previous.source_url)
                write_at(content, slot.location, source.source_url)
            if slot.source_url.startswith(PREFIX):
                msg = "missing_archived_media"
                raise ValueError(msg)
            destination.parent.mkdir(parents=True, exist_ok=True)
            working = destination.parent / f".encoding-{slot.index}"
            require_private_path(working)
            working.mkdir(exist_ok=True)
            started = time.monotonic()
            await _LOGGER.ainfo(
                "media.archive.started",
                post_id=str(post.id),
                scope=slot.scope,
                index=slot.index,
                kind=slot.kind,
                position=position,
                total=total,
            )
            converted = working / destination.name
            try:
                with structlog.contextvars.bound_contextvars(
                    post_id=str(post.id),
                    scope=slot.scope,
                    index=slot.index,
                    position=position,
                    total=total,
                ):
                    converted = await operation(source, converted) or converted
                _require_output(converted)
            except Exception as error:  # noqa: BLE001 - one failed media never discards posts
                expected = isinstance(error, MediaError)
                runs = (0 if previous is None else previous.failed_runs) + int(expected)
                category = error.category if expected else "internal"
                item = MediaFailure(
                    post_id=str(post.id),
                    scope=slot.scope,
                    index=slot.index,
                    source_url=source.source_url,
                    failed_runs=runs,
                    error=category,
                )
                _ = pending.pop(key, None)
                (failed if expected and runs >= MAX_FAILED_RUNS else pending)[key] = (
                    item
                )
                failures += 1
                await _LOGGER.awarning(
                    "media.archive.failed",
                    post_id=str(post.id),
                    scope=slot.scope,
                    index=slot.index,
                    category=category,
                    failed_runs=runs,
                    error_type=type(error).__name__ if not expected else None,
                )
                continue
            # Only validated outputs get final names; interrupted inputs stay private.
            relative = slot.path(post, extension=converted.suffix.removeprefix("."))
            destination = directory / relative
            os.link(converted, destination)
            shutil.rmtree(working)
            media[relative] = BinaryFile.inspect(destination)
            archived_paths[relative.with_suffix("")] = relative
            set_archived(content, slot, PREFIX + relative.as_posix())
            await _LOGGER.ainfo(
                "media.archive.completed",
                post_id=str(post.id),
                scope=slot.scope,
                index=slot.index,
                elapsed_seconds=round(time.monotonic() - started, 2),
            )
            _ = pending.pop(key, None)
        posts.append(post.model_copy(update={"content": content}))
    progress = progress.model_copy(
        update={
            "pending_media": tuple(pending[key] for key in sorted(pending)),
            "failed_media": tuple(failed[key] for key in sorted(failed)),
        }
    )
    await _LOGGER.ainfo(
        "media.archive.batch_completed",
        media=total,
        archived=len(media),
        failures=failures,
        pending=len(pending),
        permanently_failed=len(failed),
    )
    return replace(state, posts=tuple(posts), run_state=progress, media=media), failures
