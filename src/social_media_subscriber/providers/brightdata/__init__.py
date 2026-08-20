"""Typed Bright Data boundary and normalization contracts."""

from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.providers.brightdata.normalize import normalize_posts
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)

__all__ = [
    "BrightDataLinkedInPostSourceRecord",
    "BrightDataPost",
    "normalize_posts",
]
