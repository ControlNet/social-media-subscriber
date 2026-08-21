from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from social_media_subscriber.domain.platform import AccountKind

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from social_media_subscriber.providers.brightdata.models import BrightDataPost
    from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput


@dataclass(frozen=True, slots=True)
class AdapterClientCall:
    operation: str
    kind: AccountKind
    urls: tuple[str, ...]
    windows: tuple[tuple[date, date, bool], ...] = ()


@dataclass(slots=True)
class SyntheticBrightDataClient:
    person_posts: tuple[BrightDataPost, ...] = ()
    company_posts: tuple[BrightDataPost, ...] = ()
    failure: BaseException | None = None
    calls: list[AdapterClientCall] = field(default_factory=list)
    close_calls: int = 0

    async def aclose(self) -> None:
        self.close_calls += 1

    async def collect_person_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        self.calls.append(
            AdapterClientCall(
                "posts",
                AccountKind.PERSON,
                tuple(item.url for item in inputs),
                tuple(
                    (
                        item.start_date,
                        item.end_date,
                        True,
                    )
                    for item in inputs
                ),
            )
        )
        if self.failure is not None:
            raise self.failure
        return self.person_posts

    async def collect_company_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        self.calls.append(
            AdapterClientCall(
                "posts",
                AccountKind.COMPANY,
                tuple(item.url for item in inputs),
                tuple(
                    (
                        item.start_date,
                        item.end_date,
                        True,
                    )
                    for item in inputs
                ),
            )
        )
        if self.failure is not None:
            raise self.failure
        return self.company_posts
