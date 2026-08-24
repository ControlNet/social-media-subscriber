"""Typed Bright Data request inputs."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class PostDiscoveryInput:
    """One bounded LinkedIn account post-discovery input."""

    url: str
    start_date: date
    end_date: date

    def as_json(self, *, only_authored_posts: bool = False) -> dict[str, str | bool]:
        """Return the exact provider request object."""
        result: dict[str, str | bool] = {
            "url": self.url,
            "start_date": f"{self.start_date.isoformat()}T00:00:00.000Z",
            "end_date": f"{self.end_date.isoformat()}T23:59:59.999Z",
        }
        if only_authored_posts:
            result["only_authored_posts"] = True
        return result
