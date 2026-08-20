"""Supported social platforms and account kinds."""

from enum import StrEnum


class Platform(StrEnum):
    """External service where subscribed content is published."""

    LINKEDIN = "linkedin"


class AccountKind(StrEnum):
    """Public identity shape supported by a platform."""

    PERSON = "person"
    COMPANY = "company"
