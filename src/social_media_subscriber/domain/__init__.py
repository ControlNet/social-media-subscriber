from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.ids import (
        AccountId,
        ContentHash,
        PlatformPostId,
        PostId,
    )
    from social_media_subscriber.domain.platform import AccountKind, Platform
    from social_media_subscriber.domain.post import Post

__all__ = [
    "Account",
    "AccountId",
    "AccountKind",
    "ContentHash",
    "Platform",
    "PlatformPostId",
    "Post",
    "PostId",
]

_EXPORTS: Final = {
    "Account": ("social_media_subscriber.domain.account", "Account"),
    "AccountId": ("social_media_subscriber.domain.ids", "AccountId"),
    "AccountKind": ("social_media_subscriber.domain.platform", "AccountKind"),
    "ContentHash": ("social_media_subscriber.domain.ids", "ContentHash"),
    "Platform": ("social_media_subscriber.domain.platform", "Platform"),
    "PlatformPostId": (
        "social_media_subscriber.domain.ids",
        "PlatformPostId",
    ),
    "Post": ("social_media_subscriber.domain.post", "Post"),
    "PostId": ("social_media_subscriber.domain.ids", "PostId"),
}


def __getattr__(name: str) -> object:
    """Load public domain types without package initialization cycles."""
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = cast("object", getattr(import_module(module_name), attribute_name))
    globals()[name] = value
    return value
