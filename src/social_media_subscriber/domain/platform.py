"""Supported social platforms and account kinds."""

from datetime import date
from enum import StrEnum
from typing import Final

LINKEDIN_EARLIEST_DATE: Final = date(2003, 5, 5)
X_EARLIEST_DATE: Final = date(2006, 3, 21)


class Platform(StrEnum):
    """External service where subscribed content is published."""

    LINKEDIN = "linkedin"
    X = "x"


class AccountKind(StrEnum):
    """Public identity shape supported by a platform."""

    PERSON = "person"
    COMPANY = "company"
    PROFILE = "profile"


def earliest_collection_date(platform: Platform) -> date:
    """Return the earliest supported collection date for one platform."""
    return {
        Platform.LINKEDIN: LINKEDIN_EARLIEST_DATE,
        Platform.X: X_EARLIEST_DATE,
    }[platform]
