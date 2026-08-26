"""Canonical UTC timestamp parsing for persisted domain records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from pydantic_core import PydanticCustomError

_UTC_ERROR_CODE: Final = "utc_datetime"
_UTC_ERROR_MESSAGE: Final = "timestamp must be timezone-aware UTC"


def canonical_utc(value: datetime) -> datetime:
    """Require an aware zero-offset timestamp and normalize its UTC tzinfo."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise PydanticCustomError(
            _UTC_ERROR_CODE,
            _UTC_ERROR_MESSAGE,
        )
    return value.astimezone(UTC)


def canonical_post_timestamp(value: datetime) -> datetime:
    """Return the platform-neutral UTC publication second."""
    return canonical_utc(value).replace(microsecond=0)
