"""Renderer media locations, normalization, and immutable slot reconciliation."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from social_media_subscriber.storage.safe_directory import UnsafePathError

if TYPE_CHECKING:
    from social_media_subscriber.domain.post import Post
    from social_media_subscriber.serialization.json import JsonValue
    from social_media_subscriber.storage.snapshot import SnapshotState

PREFIX = "/social-media/"
SCOPES = (
    "main-images",
    "main-videos",
    "main-video-posters",
    "document-covers",
    "author-avatars",
    "repost-images",
    "repost-videos",
    "repost-video-posters",
    "repost-author-avatars",
    "job-logos",
    "quoted-images",
    "quoted-videos",
    "quoted-video-posters",
    "quoted-author-avatars",
)
_MEDIA_PATH = re.compile(
    r"media/(linkedin|x)/[A-Za-z0-9_-][A-Za-z0-9._-]*/("
    + "|".join(SCOPES)
    + r")/(0|[1-9][0-9]*)\.(webp|webm)\Z"
)
type Location = tuple[str | int, ...]


def object_value(value: JsonValue) -> dict[str, JsonValue]:
    """Return a JSON object or an empty object."""
    return value if isinstance(value, dict) else {}


def _items(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def read_at(content: JsonValue, location: Location) -> JsonValue:
    """Read a structural media location."""
    value = content
    for key in location:
        if isinstance(key, str) and isinstance(value, dict):
            value = value.get(key)
        elif isinstance(key, int) and isinstance(value, list) and key < len(value):
            value = value[key]
        else:
            return None
    return value


def write_at(
    content: dict[str, JsonValue], location: Location, value: JsonValue
) -> None:
    """Replace a location already established by media extraction."""
    parent = read_at(content, location[:-1])
    key = location[-1]
    if isinstance(parent, dict) and isinstance(key, str):
        parent.update({key: value})
    elif isinstance(parent, list) and isinstance(key, int):
        parent[key] = value
    else:
        raise UnsafePathError


@dataclass(frozen=True, slots=True)
class MediaSlot:
    """Stable post-local identity independent of changing signed source URLs."""

    scope: str
    index: int
    location: Location
    source_url: str
    kind: Literal["image", "video"]

    def path(self, post: Post) -> Path:
        """Build a constrained public storage path."""
        extension = "webp" if self.kind == "image" else "webm"
        path = (
            f"media/{post.platform.value}/{post.platform_post_id}/"
            f"{self.scope}/{self.index}.{extension}"
        )
        if not _MEDIA_PATH.fullmatch(path):
            raise UnsafePathError
        return Path(path)

    def key(self, post: Post) -> tuple[str, str, int]:
        """Return the queue identity shared by both deployment modes."""
        return str(post.id), self.scope, self.index


def normalize_content(post: Post) -> Post:  # noqa: C901 - closed provider alias mapping
    """Align LinkedIn aliases before extracting stable media slots."""
    content = copy.deepcopy(post.content)
    if post.platform.value == "linkedin":
        for key in ("images", "videos"):
            if key not in content:
                continue
            values = content[key]
            entries = values if isinstance(values, list) else [values]
            normalized: list[JsonValue] = []
            for entry in entries:
                value: JsonValue = {"url": entry} if isinstance(entry, str) else entry
                if key == "videos" and isinstance(value, dict) and "videoUrl" in value:
                    value["url"] = value.pop("videoUrl")
                normalized.append(value)
            content[key] = normalized
        author = object_value(content.get("author"))
        avatar = author.get("profile_image_url") or read_at(author, ("avatar", "url"))
        avatar = (
            avatar
            or content.get("user_profile_pic")
            or content.get("author_profile_pic")
        )
        if avatar:
            author["profile_image_url"] = avatar
            _ = author.pop("avatar", None)
            content["author"] = author
        _ = content.pop("user_profile_pic", None)
        _ = content.pop("author_profile_pic", None)
        document = object_value(content.get("document"))
        for source, target in (
            ("document_cover_image", "cover_image"),
            ("document_page_count", "page_count"),
        ):
            if source in content:
                _ = document.setdefault(target, content.pop(source))
        if document:
            content["document"] = document
        repost = object_value(content.get("repost"))
        video = repost.get("postVideo")
        if isinstance(video, (dict, str)):
            repost["postVideo"] = [video]
    return post.model_copy(update={"content": content})


def media_slots(post: Post) -> tuple[MediaSlot, ...]:  # noqa: C901 - closed renderer field mapping
    """Enumerate every supported media field without compacting array indices."""
    slots: list[MediaSlot] = []
    content = post.content

    def add(
        scope: str,
        index: int,
        location: Location,
        kind: Literal["image", "video"] = "image",
    ) -> None:
        value = read_at(content, location)
        if isinstance(value, str) and value:
            slots.append(MediaSlot(scope, index, location, value, kind))

    def images(location: Location, scope: str) -> None:
        for index, item in enumerate(_items(read_at(content, location))):
            add(
                scope,
                index,
                (*location, index)
                if isinstance(item, str)
                else (*location, index, "url"),
            )

    def videos(location: Location, scope: str, posters: str) -> None:
        for index, item in enumerate(_items(read_at(content, location))):
            entry = object_value(item)
            key = "url" if "url" in entry else "videoUrl"
            add(
                scope,
                index,
                (*location, index)
                if isinstance(item, str)
                else (*location, index, key),
                "video",
            )
            add(posters, index, (*location, index, "thumbnailUrl"))

    def x_media(location: Location, prefix: str) -> None:
        for index, item in enumerate(_items(read_at(content, location))):
            entry = object_value(item)
            base = (*location, index)
            if entry.get("type") == "photo":
                add(f"{prefix}-images", index, (*base, "mediaUrl"))
            elif entry.get("type") in ("video", "animated_gif"):
                add(f"{prefix}-video-posters", index, (*base, "mediaUrl"))
                selected = _selected_variant(entry.get("videoVariants"))
                if selected is not None:
                    add(
                        f"{prefix}-videos",
                        index,
                        (*base, "videoVariants", selected, "url"),
                        "video",
                    )

    if post.platform.value == "x":
        x_media(("media",), "main")
        x_media(("quotedTweet", "media"), "quoted")
        add("author-avatars", 0, ("author", "profilePicture"))
        add("quoted-author-avatars", 0, ("quotedTweet", "author", "profilePicture"))
    else:
        images(("images",), "main-images")
        videos(("videos",), "main-videos", "main-video-posters")
        if not any(
            slot.scope == "main-video-posters" and slot.index == 0 for slot in slots
        ):
            add("main-video-posters", 0, ("video_thumbnail",))
        add("author-avatars", 0, ("author", "profile_image_url"))
        add("document-covers", 0, ("document", "cover_image"))
        images(("repost", "postImages"), "repost-images")
        videos(("repost", "postVideo"), "repost-videos", "repost-video-posters")
        add("repost-author-avatars", 0, ("repost", "author", "avatar", "url"))
        add("job-logos", 0, ("repost", "job", "logoUrl"))
    return tuple(slots)


def _selected_variant(variants: JsonValue) -> int | None:
    choices: list[tuple[bool, int, int]] = []
    for index, variant in enumerate(_items(variants)):
        value = object_value(variant)
        url = value.get("url")
        if value.get("contentType") in ("video/mp4", "video/webm") and isinstance(
            url, str
        ):
            bitrate = value.get("bitrate")
            choices.append(
                (
                    url.startswith(PREFIX),
                    bitrate if isinstance(bitrate, int) else -1,
                    -index,
                )
            )
    return -max(choices)[2] if choices else None


def set_archived(content: dict[str, JsonValue], slot: MediaSlot, url: str) -> None:
    """Rewrite one slot, including the selected X variant MIME type."""
    write_at(content, slot.location, url)
    if "videoVariants" in slot.location:
        write_at(content, (*slot.location[:-1], "contentType"), "video/webm")


def reconcile_media(previous: Post, current: Post) -> Post:
    """Keep missing media structures and prefer previously archived URLs."""
    old = normalize_content(previous)
    new = normalize_content(current)
    content = copy.deepcopy(new.content)
    for slot in media_slots(old):
        _restore_location(content, old.content, slot.location)
    fresh = new.model_copy(update={"content": content})
    old_slots = {(slot.scope, slot.index): slot for slot in media_slots(old)}
    for slot in media_slots(fresh):
        prior = old_slots.get((slot.scope, slot.index))
        if prior is not None and prior.source_url.startswith(PREFIX):
            set_archived(content, slot, prior.source_url)
    return fresh


def _restore_location(
    current: JsonValue, previous: JsonValue, location: Location
) -> None:
    if not location:
        return
    key, *rest = location
    if (
        isinstance(key, str)
        and isinstance(current, dict)
        and isinstance(previous, dict)
    ):
        if current.get(key) is None:
            current[key] = copy.deepcopy(previous.get(key))
        else:
            _restore_location(current[key], previous.get(key), tuple(rest))
    elif (
        isinstance(key, int)
        and isinstance(current, list)
        and isinstance(previous, list)
    ):
        while len(current) <= key:
            current.append(copy.deepcopy(previous[len(current)]))
        if current[key] is None:
            current[key] = copy.deepcopy(previous[key])
        else:
            _restore_location(current[key], previous[key], tuple(rest))


def validate_media_inventory(state: SnapshotState) -> None:
    """Reject unsafe paths and dangling owned references without probing old media."""
    for path in state.media:
        if not _MEDIA_PATH.fullmatch(path.as_posix()):
            raise UnsafePathError
    for post in state.posts:
        for slot in media_slots(post):
            if slot.source_url.startswith(PREFIX):
                path = Path(slot.source_url.removeprefix(PREFIX))
                if path != slot.path(post) or path not in state.media:
                    raise UnsafePathError
