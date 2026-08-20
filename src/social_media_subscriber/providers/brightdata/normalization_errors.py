"""Safe typed failures for Bright Data normalization integrity boundaries."""

from dataclasses import dataclass
from enum import StrEnum
from typing import override


class BrightDataNormalizationErrorCategory(StrEnum):
    """Machine-readable provider normalization failure categories."""

    IDENTITY = "identity"
    OWNERSHIP = "ownership"
    DUPLICATE = "duplicate"
    POST_URL = "post_url"


@dataclass(frozen=True, slots=True)
class BrightDataNormalizationError(Exception):
    """Normalization failure containing no provider values or raw URLs."""

    category: BrightDataNormalizationErrorCategory

    @override
    def __str__(self) -> str:
        """Render stable category-only diagnostics safe for logs."""
        return f"Bright Data normalization failed ({self.category.value})"
