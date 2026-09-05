"""Expected media failures eligible for the bounded retry queue."""

from typing import Literal

type MediaErrorCategory = Literal[
    "source", "http", "mime", "size", "empty", "redirect", "download", "conversion"
]


class MediaError(ValueError):
    """A classified source or conversion failure, not an internal program error."""

    category: MediaErrorCategory

    def __init__(self, category: MediaErrorCategory) -> None:
        """Expose only an application-owned category, never raw provider text."""
        super().__init__(category)
        self.category = category
