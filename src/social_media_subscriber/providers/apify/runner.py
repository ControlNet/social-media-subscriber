"""Shared accepted-run-safe Apify Actor transport."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Self, final

import anyio
import httpx2
from pydantic import TypeAdapter, ValidationError

from social_media_subscriber.providers.apify.constants import (
    ACTOR_WAIT_SECONDS,
    RUN_POLL_SECONDS,
    RUN_TIMEOUT_SECONDS,
)
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
    categorize_http_status,
)
from social_media_subscriber.providers.apify.models import ApifyRun, ApifyRunEnvelope
from social_media_subscriber.providers.http import (
    HttpClientConfig,
    create_async_http_client,
)
from social_media_subscriber.serialization.json import JsonValue

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType

type ActorInput = dict[str, bool | int | str | list[str]]

_DEFAULT_HTTP_CONFIG: Final = HttpClientConfig(base_url="https://api.apify.com")
_ACTIVE_STATUSES: Final = frozenset({"READY", "RUNNING"})
_SUCCESS_STATUS: Final = "SUCCEEDED"
_DATASET_PAGE_SIZE: Final = 1_000
_JSON_VALUES: Final[TypeAdapter[list[JsonValue]]] = TypeAdapter(list[JsonValue])


@final
class ApifyActorRunner:
    """Run one Actor at a time through one credential-bound transport."""

    def __init__(
        self,
        api_key: str,
        config: HttpClientConfig = _DEFAULT_HTTP_CONFIG,
        *,
        sleeper: Callable[[float], Awaitable[None]] = anyio.sleep,
        run_timeout_seconds: float = RUN_TIMEOUT_SECONDS,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a credential-bound Actor runner."""
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

    async def run(
        self,
        actor: str,
        actor_input: ActorInput,
    ) -> ApifyRun:
        """Start one Actor and return a successful accepted run."""
        params = {"waitForFinish": str(ACTOR_WAIT_SECONDS)}
        try:
            response = await self._request(
                "POST",
                f"/v2/acts/{actor}/runs",
                params=params,
                json=actor_input,
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
        if run.status != _SUCCESS_STATUS or run.default_dataset_id is None:
            raise ApifyError(ApifyErrorCategory.RUN_TERMINAL, run_accepted=True)
        return run

    async def download_dataset(self, dataset_id: str) -> tuple[JsonValue, ...]:
        """Download every clean dataset page from an accepted run."""
        values: list[JsonValue] = []
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
                page = _JSON_VALUES.validate_json(response.content)
            except ValidationError:
                raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=True) from None
            values.extend(page)
            if len(page) < _DATASET_PAGE_SIZE:
                return tuple(values)
            offset += len(page)

    async def download_record(self, store_id: str, key: str) -> bytes:
        """Download one accepted run's key-value store record."""
        response = await self._accepted_request(
            "GET", f"/v2/key-value-stores/{store_id}/records/{key}"
        )
        return response.content

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
        json: ActorInput | None = None,
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
