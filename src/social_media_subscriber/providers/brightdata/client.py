"""Exact Bright Data LinkedIn REST client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Self, final

import anyio
import httpx2
import structlog
from pydantic import ConfigDict, TypeAdapter, ValidationError

from social_media_subscriber.providers.brightdata.constants import (
    COMPANY_IDENTITY_DATASET,
    LINKEDIN_POSTS_DATASET,
    MAX_SYNC_BATCH_SIZE,
    PERSON_IDENTITY_DATASET,
    SNAPSHOT_POLL_SECONDS,
    SNAPSHOT_TIMEOUT_SECONDS,
    STATUS_RETRY_DELAYS,
)
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
    categorize_http_status,
)
from social_media_subscriber.providers.brightdata.models import (
    BrightDataCompanyIdentity,
    BrightDataPersonIdentity,
    BrightDataPost,
    BrightDataSnapshotEnvelope,
    BrightDataSnapshotProgress,
    JsonValue,
)
from social_media_subscriber.providers.brightdata.parsing import parse_items
from social_media_subscriber.providers.http import (
    HttpClientConfig,
    create_async_http_client,
)

_JSON: Final[TypeAdapter[JsonValue]] = TypeAdapter(
    JsonValue, config=ConfigDict(strict=True)
)
_LOGGER = structlog.stdlib.get_logger()
_ACTIVE_SNAPSHOT_STATUSES: Final = frozenset({"building", "pending", "running"})
_TERMINAL_SNAPSHOT_STATUSES: Final = frozenset({"canceled", "cancelled", "failed"})
_DEFAULT_HTTP_CONFIG: Final = HttpClientConfig()

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from types import TracebackType

    from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput


@final
class BrightDataClient:
    """One credential-bound client for synchronous and snapshot responses."""

    def __init__(
        self,
        api_key: str,
        config: HttpClientConfig = _DEFAULT_HTTP_CONFIG,
        *,
        sleeper: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        """Create a client bound to exactly one API key."""
        self._http = create_async_http_client(api_key, config)
        self._sleeper = sleeper

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

    async def resolve_person_identities(
        self, urls: Sequence[str]
    ) -> tuple[BrightDataPersonIdentity, ...]:
        """Resolve personal LinkedIn profile identities."""
        return await self._scrape(
            dataset=PERSON_IDENTITY_DATASET,
            mode=None,
            body=[{"url": url} for url in self._bounded(urls)],
            item_type=BrightDataPersonIdentity,
        )

    async def resolve_company_identities(
        self, urls: Sequence[str]
    ) -> tuple[BrightDataCompanyIdentity, ...]:
        """Resolve LinkedIn company identities."""
        return await self._scrape(
            dataset=COMPANY_IDENTITY_DATASET,
            mode=None,
            body=[{"url": url} for url in self._bounded(urls)],
            item_type=BrightDataCompanyIdentity,
        )

    async def collect_person_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        """Collect authored posts for personal profiles."""
        return await self._collect_posts(inputs, "profile_url")

    async def collect_company_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        """Collect authored posts for company profiles."""
        return await self._collect_posts(inputs, "company_url")

    async def _collect_posts(
        self,
        inputs: Sequence[PostDiscoveryInput],
        mode: str,
    ) -> tuple[BrightDataPost, ...]:
        bounded = self._bounded(inputs)
        if any(item.start_date > item.end_date for item in bounded):
            raise BrightDataError(BrightDataErrorCategory.INPUT)
        return await self._scrape(
            dataset=LINKEDIN_POSTS_DATASET,
            mode=mode,
            body=[item.as_json() for item in bounded],
            item_type=BrightDataPost,
        )

    @staticmethod
    def _bounded[ItemT](items: Sequence[ItemT]) -> tuple[ItemT, ...]:
        result = tuple(items)
        if not 1 <= len(result) <= MAX_SYNC_BATCH_SIZE:
            raise BrightDataError(BrightDataErrorCategory.INPUT)
        return result

    async def _scrape[ModelT](
        self,
        *,
        dataset: str,
        mode: str | None,
        body: list[dict[str, str | bool]],
        item_type: type[ModelT],
    ) -> tuple[ModelT, ...]:
        params = {"dataset_id": dataset, "include_errors": "true"}
        if mode is not None:
            params.update({"type": "discover_new", "discover_by": mode})
        response = await self._request("POST", "/datasets/v3/scrape", params, body)
        value = self._json(response)
        if isinstance(value, list):
            return parse_items(value, item_type, snapshot_accepted=False)
        try:
            envelope = BrightDataSnapshotEnvelope.model_validate(value)
        except ValidationError:
            raise BrightDataError(BrightDataErrorCategory.SCHEMA) from None
        await _LOGGER.ainfo(
            "provider.snapshot.accepted", endpoint="scrape", batch_count=len(body)
        )
        return await self._await_snapshot(envelope.snapshot_id, item_type)

    async def _await_snapshot[ModelT](
        self, snapshot_id: str, item_type: type[ModelT]
    ) -> tuple[ModelT, ...]:
        polls = int(SNAPSHOT_TIMEOUT_SECONDS / SNAPSHOT_POLL_SECONDS)
        for _poll in range(polls):
            await self._sleeper(SNAPSHOT_POLL_SECONDS)
            response = await self._snapshot_request(
                "GET", f"/datasets/v3/progress/{snapshot_id}", None, None
            )
            try:
                progress = BrightDataSnapshotProgress.model_validate(
                    self._json(response)
                )
            except ValidationError:
                raise BrightDataError(
                    BrightDataErrorCategory.SCHEMA, snapshot_accepted=True
                ) from None
            status = progress.status.casefold()
            if status == "ready":
                downloaded = await self._snapshot_request(
                    "GET", f"/datasets/v3/snapshot/{snapshot_id}", None, None
                )
                value = self._json(downloaded)
                if not isinstance(value, list):
                    raise BrightDataError(
                        BrightDataErrorCategory.SCHEMA, snapshot_accepted=True
                    )
                return parse_items(value, item_type, snapshot_accepted=True)
            if status in _TERMINAL_SNAPSHOT_STATUSES:
                raise BrightDataError(
                    BrightDataErrorCategory.SNAPSHOT_TERMINAL,
                    snapshot_accepted=True,
                )
            if status not in _ACTIVE_SNAPSHOT_STATUSES:
                raise BrightDataError(
                    BrightDataErrorCategory.SCHEMA, snapshot_accepted=True
                )
        raise BrightDataError(
            BrightDataErrorCategory.SNAPSHOT_TIMEOUT, snapshot_accepted=True
        )

    async def _snapshot_request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None,
        body: list[dict[str, str | bool]] | None,
    ) -> httpx2.Response:
        try:
            return await self._request(method, path, params, body)
        except BrightDataError as error:
            raise BrightDataError(
                error.category, status=error.status, snapshot_accepted=True
            ) from None

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None,
        body: list[dict[str, str | bool]] | None,
    ) -> httpx2.Response:
        for attempt in range(len(STATUS_RETRY_DELAYS) + 1):
            try:
                response = await self._http.request(
                    method, path, params=params, json=body
                )
            except httpx2.TimeoutException:
                raise BrightDataError(BrightDataErrorCategory.TIMEOUT) from None
            except (httpx2.ConnectError, httpx2.NetworkError):
                raise BrightDataError(BrightDataErrorCategory.RETRYABLE) from None
            category = categorize_http_status(response.status_code)
            if category is None:
                return response
            if category is BrightDataErrorCategory.RETRYABLE and attempt < len(
                STATUS_RETRY_DELAYS
            ):
                _LOGGER.warning(
                    "provider.http.retry",
                    method=method,
                    endpoint="scrape" if path == "/datasets/v3/scrape" else "snapshot",
                    status=response.status_code,
                    category=category,
                )
                await self._sleeper(STATUS_RETRY_DELAYS[attempt])
                continue
            raise BrightDataError(category, status=response.status_code)
        raise BrightDataError(BrightDataErrorCategory.RETRYABLE)

    @staticmethod
    def _json(response: httpx2.Response) -> JsonValue:
        try:
            return _JSON.validate_json(response.content)
        except ValidationError:
            raise BrightDataError(BrightDataErrorCategory.SCHEMA) from None
