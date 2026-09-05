"""Best-effort media archival and persistent retry queue transitions."""

from __future__ import annotations

import copy
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import replace
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
from social_media_subscriber.storage.binary import BinaryFile
from social_media_subscriber.storage.run_state import MediaFailure, RunState

if TYPE_CHECKING:
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.storage.snapshot import SnapshotState

type Materializer = Callable[[MediaSlot, Path], Awaitable[None]]
_LOGGER = structlog.stdlib.get_logger()
MAX_FAILED_RUNS = 3


def _require_output(path: Path) -> None:
    if not path.is_file() or not path.stat().st_size:
        raise MediaError(category="conversion")


async def archive_media(
    state: SnapshotState,
    directory: Path,
    *,
    materialize: Materializer | None = None,
    refreshed_post_ids: frozenset[str] = frozenset(),  # pyright: ignore[reportCallInDefaultInitializer]
) -> tuple[SnapshotState, int]:
    """Archive unseen slots; retain every post and source URL on failure."""
    operation = materialize or materialize_media
    progress = state.run_state or RunState()
    pending = {
        (item.post_id, item.scope, item.index): item for item in progress.pending_media
    }
    failed = {
        (item.post_id, item.scope, item.index): item for item in progress.failed_media
    }
    media = dict(state.media)
    posts: list[Post] = []
    failures = 0
    for original in state.posts:
        post = normalize_content(original)
        content = copy.deepcopy(post.content)
        for slot in media_slots(post):
            key = slot.key(post)
            relative = slot.path(post)
            destination = directory / relative
            if relative in media:
                set_archived(content, slot, PREFIX + relative.as_posix())
                _ = pending.pop(key, None)
                _ = failed.pop(key, None)
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
            with tempfile.TemporaryDirectory(
                prefix=".encoding-", dir=destination.parent
            ) as temporary:
                converted = Path(temporary) / destination.name
                try:
                    await operation(source, converted)
                    _require_output(converted)
                except Exception as error:  # noqa: BLE001 - one failed media never discards posts
                    expected = isinstance(error, MediaError)
                    runs = (0 if previous is None else previous.failed_runs) + int(
                        expected
                    )
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
                    (failed if expected and runs >= MAX_FAILED_RUNS else pending)[
                        key
                    ] = item
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
                # A slot is exposed only once its complete output has been validated.
                os.link(converted, destination)
            media[relative] = BinaryFile.inspect(destination)
            set_archived(content, slot, PREFIX + relative.as_posix())
            _ = pending.pop(key, None)
        posts.append(post.model_copy(update={"content": content}))
    progress = progress.model_copy(
        update={
            "pending_media": tuple(pending[key] for key in sorted(pending)),
            "failed_media": tuple(failed[key] for key in sorted(failed)),
        }
    )
    return replace(state, posts=tuple(posts), run_state=progress, media=media), failures
