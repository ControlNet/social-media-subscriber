"""Supported social platforms and account kinds."""

from datetime import date
from enum import StrEnum
from typing import Final

LINKEDIN_EARLIEST_DATE: Final = date(2003, 5, 5)


class Platform(StrEnum):
    """External service where subscribed content is published."""

    LINKEDIN = "linkedin"


class AccountKind(StrEnum):
    """Public identity shape supported by a platform."""

    PERSON = "person"
    COMPANY = "company"
