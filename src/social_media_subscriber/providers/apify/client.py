"""Apify REST client for the approved LinkedIn profile-post Actor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Self, final

import anyio
import httpx2
from pydantic import TypeAdapter, ValidationError

from social_media_subscriber.providers.apify.constants import (
    ACTOR_WAIT_SECONDS,
    APIFY_ACTOR,
    RUN_POLL_SECONDS,
    RUN_TIMEOUT_SECONDS,
)
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
    categorize_http_status,
)
from social_media_subscriber.providers.apify.models import (
    ApifyPost,
    ApifyRun,
    ApifyRunEnvelope,
)
from social_media_subscriber.providers.http import (
    HttpClientConfig,
    create_async_http_client,
)
from social_media_subscriber.serialization.json import JsonValue

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType

    from social_media_subscriber.providers.apify.requests import ApifyPostInput

_DEFAULT_HTTP_CONFIG: Final = HttpClientConfig(base_url="https://api.apify.com")
_ACTIVE_STATUSES: Final = frozenset({"READY", "RUNNING"})
_SUCCESS_STATUS: Final = "SUCCEEDED"
_DATASET_PAGE_SIZE: Final = 1_000
_JSON_VALUES: Final[TypeAdapter[list[JsonValue]]] = TypeAdapter(list[JsonValue])
_POSTS: Final[TypeAdapter[tuple[ApifyPost, ...]]] = TypeAdapter(tuple[ApifyPost, ...])


@final
class ApifyClient:
    """One credential-bound Actor runner and dataset reader."""

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
        self._http = create_async_http_client(api_key, config, transport=transport)
        self._sleeper = sleeper
        self._run_timeout_seconds = run_timeout_seconds

    async def __aenter__(self) -> Self:
        """Open the owned connection pool."""
        _ = await self._http.__aenter__()
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
        await self._http.aclose()

    async def collect_posts(self, request: ApifyPostInput) -> tuple[ApifyPost, ...]:
        """Run one profile scrape and return only posts inside its end date."""
        if request.start_date > request.end_date:
            raise ApifyError(ApifyErrorCategory.INPUT)
        try:
            response = await self._request(
                "POST",
                f"/v2/acts/{APIFY_ACTOR}/runs",
                params={"waitForFinish": str(ACTOR_WAIT_SECONDS)},
                json=request.as_json(),
            )
        except ApifyError as error:
            if error.status is None and error.category in {
                ApifyErrorCategory.RETRYABLE,
                ApifyErrorCategory.TIMEOUT,
            }:
                raise ApifyError(
                    error.category, status=error.status, run_accepted=True
                ) from None
            raise
        run = self._parse_run(response, accepted=True)
        run = await self._await_run(run)
        dataset_id = run.default_dataset_id
        if run.status != _SUCCESS_STATUS or dataset_id is None:
            raise ApifyError(ApifyErrorCategory.RUN_TERMINAL, run_accepted=True)
        posts = await self._download_dataset(dataset_id)
        return tuple(
            post
            for post in posts
            if request.start_date <= post.posted_at.timestamp.date() <= request.end_date
        )

    async def _download_dataset(self, dataset_id: str) -> tuple[ApifyPost, ...]:
        posts: list[ApifyPost] = []
        offset = 0
        while True:
            response = await self._accepted_request(
                "GET",
                f"/v2/datasets/{dataset_id}/items",
                params={
                    "clean": "true",
                    "format": "json",
                    "limit": str(_DATASET_PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            try:
                values = _JSON_VALUES.validate_json(response.content)
                page = _POSTS.validate_python(values)
            except ValidationError:
                raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=True) from None
            posts.extend(page)
            if len(page) < _DATASET_PAGE_SIZE:
                return tuple(posts)
            offset += len(page)

    async def _await_run(self, run: ApifyRun) -> ApifyRun:
        polls = max(1, int(self._run_timeout_seconds / RUN_POLL_SECONDS))
        for _poll in range(polls):
            if run.status not in _ACTIVE_STATUSES:
                return run
            await self._sleeper(RUN_POLL_SECONDS)
            response = await self._accepted_request("GET", f"/v2/actor-runs/{run.id}")
            run = self._parse_run(response, accepted=True)
        raise ApifyError(ApifyErrorCategory.TIMEOUT, run_accepted=True)

    @staticmethod
    def _parse_run(response: httpx2.Response, *, accepted: bool) -> ApifyRun:
        try:
            return ApifyRunEnvelope.model_validate_json(response.content).data
        except ValidationError:
            raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=accepted) from None

    async def _accepted_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx2.Response:
        try:
            return await self._request(method, path, params=params)
        except ApifyError as error:
            raise ApifyError(
                error.category, status=error.status, run_accepted=True
            ) from None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, bool | int | str | list[str]] | None = None,
    ) -> httpx2.Response:
        try:
            response = await self._http.request(method, path, params=params, json=json)
        except httpx2.TimeoutException:
            raise ApifyError(ApifyErrorCategory.TIMEOUT) from None
        except (httpx2.ConnectError, httpx2.NetworkError):
            raise ApifyError(ApifyErrorCategory.RETRYABLE) from None
        category = categorize_http_status(response.status_code)
        if category is not None:
            raise ApifyError(category, status=response.status_code)
        return response
