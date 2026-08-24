"""Frozen outcomes produced by pure Bright Data normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from social_media_subscriber.domain.post import Post


@dataclass(frozen=True, slots=True)
class BrightDataNormalizationResult:
    """Complete canonical Posts produced without performing I/O."""

    posts: tuple[Post, ...]
