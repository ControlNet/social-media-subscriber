"""Structural adapter driver contract."""

from __future__ import annotations

from typing import Protocol

from social_media_subscriber.adapters.metadata import AdapterMetadataOwner


class AdapterDriver(AdapterMetadataOwner, Protocol):
    """Structural contract shared by automatic adapter driver classes."""
