"""Secret-safe failures raised at the account input boundary."""

from dataclasses import dataclass
from enum import StrEnum
from typing import override


class AccountInputErrorCategory(StrEnum):
    """Machine-readable account input failure category."""

    EMPTY_ACCOUNTS = "empty_accounts"
    EMPTY_BRIGHT_DATA_API_KEYS = "empty_bright_data_api_keys"
    INVALID_ACCOUNT_URL = "invalid_account_url"


class AccountInputField(StrEnum):
    """Runtime setting associated with an account input failure."""

    ACCOUNTS = "accounts"
    BRIGHT_DATA_API_KEYS = "bright_data_api_keys"


@dataclass(frozen=True, slots=True)
class AccountInputError(Exception):
    """Typed account input failure that never carries rejected values."""

    category: AccountInputErrorCategory
    field: AccountInputField

    @override
    def __str__(self) -> str:
        """Return only category metadata safe for logs and exceptions."""
        return f"invalid account input ({self.field.value}: {self.category.value})"
