"""Machine-readable adapter metadata validation failures."""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class MetadataViolation(StrEnum):
    """Machine-readable reasons an adapter descriptor is invalid."""

    EMPTY_OPERATIONS = "empty_operations"
    EMPTY_ACCOUNT_KINDS = "empty_account_kinds"
    DUPLICATE_OPERATIONS = "duplicate_operations"
    DUPLICATE_ACCOUNT_KINDS = "duplicate_account_kinds"
