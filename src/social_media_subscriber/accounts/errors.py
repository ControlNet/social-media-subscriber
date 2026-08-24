"""Secret-safe failures raised at the account input boundary."""

from dataclasses import dataclass
from enum import StrEnum
from typing import override


class AccountInputErrorCategory(StrEnum):
    """Machine-readable runtime input failure category."""

    EMPTY_ACCOUNTS = "empty_accounts"
    EMPTY_SOURCES = "empty_sources"
    INVALID_ACCOUNT_URL = "invalid_account_url"
    INVALID_SOURCE = "invalid_source"
    UNSUPPORTED_SOURCE = "unsupported_source"


class AccountInputField(StrEnum):
    """Runtime setting associated with an input failure."""

    ACCOUNTS = "accounts"
    SOURCES = "sources"


@dataclass(frozen=True, slots=True)
class AccountInputError(Exception):
    """Typed runtime input failure that never carries rejected values."""

    category: AccountInputErrorCategory
    field: AccountInputField

    @override
    def __str__(self) -> str:
        """Return only category metadata safe for logs and exceptions."""
        return f"invalid runtime input ({self.field.value}: {self.category.value})"
