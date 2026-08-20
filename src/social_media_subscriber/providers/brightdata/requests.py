"""Typed Bright Data request inputs."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class PostDiscoveryInput:
    """One bounded LinkedIn account post-discovery input."""

    url: str
    start_date: date
    end_date: date

    def as_json(self) -> dict[str, str | bool]:
        """Return the exact provider request object."""
        return {
            "url": self.url,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "only_authored_posts": True,
        }
