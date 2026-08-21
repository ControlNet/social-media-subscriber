from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from pydantic import SecretStr

from social_media_subscriber.application.collect import (
    CollectionRequest,
    collect_snapshot,
)
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from social_media_subscriber.application.results import CollectionResult
    from social_media_subscriber.providers.brightdata.errors import BrightDataError
    from social_media_subscriber.providers.brightdata.requests import (
        PostDiscoveryInput,
    )

RUN_START = datetime(2026, 8, 20, 12, tzinfo=UTC)
PERSON_URL = "https://www.linkedin.com/in/synthetic-person/"
COMPANY_URL = "https://www.linkedin.com/company/synthetic-company/"


def settings(*urls: str, keys: str = "synthetic-key") -> Settings:
    return Settings(
        accounts=SecretStr("\n".join(urls)),
        bright_data_api_keys=SecretStr(keys),
    )


def post(
    post_id: str = "activity-1",
    *,
    actor_url: str = PERSON_URL,
    provider_user_id: str | None = "synthetic-provider-user",
    text: str = "Synthetic post",
    likes: int = 1,
) -> BrightDataPost:
    return BrightDataPost.model_validate(
        {
            "id": post_id,
            "date_posted": "2026-08-18T12:00:00+00:00",
            "post_type": "post",
            "url": f"https://www.linkedin.com/posts/{post_id}/",
            "profile_url": actor_url,
            "user_id": provider_user_id,
            "post_text": text,
            "num_likes": likes,
        }
    )


@dataclass(slots=True)
class ApplicationClient:
    person_posts: tuple[BrightDataPost, ...] = field(default_factory=lambda: (post(),))
    company_posts: tuple[BrightDataPost, ...] = ()
    person_failure: BrightDataError | None = None
    company_failure: BrightDataError | None = None
    calls: list[tuple[str, tuple[tuple[date, date], ...]]] = field(default_factory=list)
    close_calls: int = 0

    async def aclose(self) -> None:
        self.close_calls += 1

    async def collect_person_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        self.calls.append(
            ("person_posts", tuple((item.start_date, item.end_date) for item in inputs))
        )
        if self.person_failure is not None:
            raise self.person_failure
        return self.person_posts

    async def collect_company_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        self.calls.append(
            (
                "company_posts",
                tuple((item.start_date, item.end_date) for item in inputs),
            )
        )
        if self.company_failure is not None:
            raise self.company_failure
        return self.company_posts


def request(
    root: Path,
    settings: Settings,
    *,
    paths: tuple[str, str] = ("previous", "candidate"),
    window: tuple[date | None, date | None] = (None, None),
) -> CollectionRequest:
    return CollectionRequest(
        settings=settings,
        previous_snapshot_dir=root / paths[0],
        candidate_snapshot_dir=root / paths[1],
        run_started_at=RUN_START,
        start_date=window[0],
        end_date=window[1],
    )


async def run(
    request: CollectionRequest,
    clients: tuple[ApplicationClient, ...],
) -> CollectionResult:
    remaining = iter(clients)
    return await collect_snapshot(request, lambda _credential: next(remaining))


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
