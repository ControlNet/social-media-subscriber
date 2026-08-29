"""Apify REST client for the approved LinkedIn profile-post Actor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Self, final

import anyio
from pydantic import TypeAdapter, ValidationError

from social_media_subscriber.providers.apify.constants import (
    APIFY_ACTOR,
    RUN_TIMEOUT_SECONDS,
)
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.providers.apify.models import ApifyPost
from social_media_subscriber.providers.apify.runner import ApifyActorRunner
from social_media_subscriber.providers.http import HttpClientConfig

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType

    import httpx2

    from social_media_subscriber.providers.apify.requests import ApifyPostInput

_DEFAULT_HTTP_CONFIG: Final = HttpClientConfig(base_url="https://api.apify.com")
_POSTS: Final[TypeAdapter[tuple[ApifyPost, ...]]] = TypeAdapter(tuple[ApifyPost, ...])


@final
class ApifyClient:
    """One credential-bound LinkedIn Actor client."""

    def __init__(
        self,
        api_key: str,
        config: HttpClientConfig = _DEFAULT_HTTP_CONFIG,
        *,
        sleeper: Callable[[float], Awaitable[None]] = anyio.sleep,
        run_timeout_seconds: float = RUN_TIMEOUT_SECONDS,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a credential-bound Actor client."""
        self._runner = ApifyActorRunner(
            api_key,
            config,
            sleeper=sleeper,
            run_timeout_seconds=run_timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        """Open the owned connection pool."""
        _ = await self._runner.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the owned connection pool."""
        _ = exc_type, exc_value, traceback
        await self.aclose()

    async def aclose(self) -> None:
        """Close the owned connection pool."""
        await self._runner.aclose()

    async def collect_posts(self, request: ApifyPostInput) -> tuple[ApifyPost, ...]:
        """Run one profile scrape and return only posts inside its end date."""
        if request.start_date > request.end_date:
            raise ApifyError(ApifyErrorCategory.INPUT)
        run = await self._runner.run(APIFY_ACTOR, request.as_json())
        dataset_id = run.default_dataset_id
        if dataset_id is None:
            raise ApifyError(ApifyErrorCategory.RUN_TERMINAL, run_accepted=True)
        values = await self._runner.download_dataset(dataset_id)
        try:
            posts = _POSTS.validate_python(values)
        except ValidationError:
            raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=True) from None
        return tuple(
            post
            for post in posts
            if request.start_date <= post.posted_at.timestamp.date() <= request.end_date
        )
