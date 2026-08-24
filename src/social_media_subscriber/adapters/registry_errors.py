"""Typed construction failures for the explicit adapter registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from social_media_subscriber.adapters.metadata_errors import MetadataViolation


@dataclass(frozen=True, slots=True)
class MissingAdapterMetadataError(Exception):
    """Raised when an explicit registry entry was not decorated."""

    driver_name: str

    @override
    def __str__(self) -> str:
        """Return an actionable driver-only diagnostic."""
        return f"adapter driver {self.driver_name!r} has no capability metadata"


@dataclass(frozen=True, slots=True)
class InvalidAdapterMetadataError(Exception):
    """Raised when decorated metadata contains an invalid capability set."""

    driver_name: str
    violation: MetadataViolation

    @override
    def __str__(self) -> str:
        """Return an actionable driver and violation diagnostic."""
        return (
            f"adapter driver {self.driver_name!r} has invalid metadata: "
            f"{self.violation.value}"
        )


@dataclass(frozen=True, slots=True)
class DuplicateAdapterDriverError(Exception):
    """Raised when the same driver class appears twice in a registry."""

    driver_name: str
    first_index: int
    duplicate_index: int

    @override
    def __str__(self) -> str:
        """Return an actionable duplicate-position diagnostic."""
        return (
            f"adapter driver {self.driver_name!r} is repeated at positions "
            f"{self.first_index} and {self.duplicate_index}"
        )
