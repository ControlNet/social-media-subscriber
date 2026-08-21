from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

import anyio
import httpx2
import pytest
from pydantic import SecretStr

from social_media_subscriber.accounts.input import AccountInput
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.application.collect import (
    CollectionRequest,
    collect_snapshot,
)
from social_media_subscriber.application.results import CollectionExitCode
from social_media_subscriber.bootstrap import bootstrap_runtime
from social_media_subscriber.providers.brightdata import client as client_module
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataAdapterConfig,
)
from social_media_subscriber.providers.brightdata.constants import (
    PERSON_IDENTITY_DATASET,
)
from social_media_subscriber.providers.http import HttpClientConfig
from social_media_subscriber.settings import Settings

if TYPE_CHECKING:
    from pathlib import Path


_PERSON_URL = "https://www.linkedin.com/in/lifecycle-person/"
_RUN_STARTED_AT = datetime(2026, 8, 20, 12, tzinfo=UTC)
_DEFAULT_HTTP_CONFIG = HttpClientConfig()

type _ResponseHandler = (
    Callable[[httpx2.Request], httpx2.Response]
    | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]
)


class _CloseCountingClient(httpx2.AsyncClient):
    close_calls: int

    def __init__(self, handler: _ResponseHandler) -> None:
        super().__init__(
            base_url="https://provider.invalid",
            transport=httpx2.MockTransport(handler),
        )
        self.close_calls = 0

    @override
    async def aclose(self) -> None:
        self.close_calls += 1
        await super().aclose()


@dataclass(slots=True)
class _TrackingHttpFactory:
    handler: _ResponseHandler
    clients: list[_CloseCountingClient] = field(default_factory=list)

    def create(
        self,
        api_key: str,
        config: HttpClientConfig = _DEFAULT_HTTP_CONFIG,
    ) -> httpx2.AsyncClient:
        _ = api_key, config
        client = _CloseCountingClient(self.handler)
        self.clients.append(client)
        return client


def _request(root: Path, keys: str = "lifecycle-test-key") -> CollectionRequest:
    return CollectionRequest(
        settings=Settings(
            accounts=SecretStr(_PERSON_URL),
            bright_data_api_keys=SecretStr(keys),
        ),
        previous_snapshot_dir=root / "previous",
        candidate_snapshot_dir=root / "candidate",
        run_started_at=_RUN_STARTED_AT,
    )


def _success_handler(request: httpx2.Request) -> httpx2.Response:
    if request.url.params.get("dataset_id") == PERSON_IDENTITY_DATASET:
        payload = [{"linkedin_num_id": "101", "url": _PERSON_URL}]
    else:
        payload = [
            {
                "id": "activity-1",
                "date_posted": "2026-08-18T12:00:00+00:00",
                "post_type": "post",
                "url": "https://www.linkedin.com/posts/activity-1/",
                "user_id": "101",
            }
        ]
    return httpx2.Response(200, json=payload)


@pytest.mark.anyio
async def test_brightdata_context_manager_delegates_to_owned_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    transport_factory = _TrackingHttpFactory(_success_handler)
    monkeypatch.setattr(
        client_module,
        "create_async_http_client",
        transport_factory.create,
    )
    client = client_module.BrightDataClient("lifecycle-test-key")

    # When
    async with client:
        pass

    # Then
    assert transport_factory.clients[0].is_closed
    assert transport_factory.clients[0].close_calls == 1


@pytest.mark.anyio
async def test_runtime_close_is_idempotent_for_every_unique_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    transport_factory = _TrackingHttpFactory(_success_handler)
    monkeypatch.setattr(
        client_module,
        "create_async_http_client",
        transport_factory.create,
    )
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(parse_linkedin_locator(_PERSON_URL),),
            bright_data_api_keys=(
                SecretStr("lifecycle-first"),
                SecretStr("lifecycle-first"),
                SecretStr("lifecycle-second"),
            ),
        ),
        BrightDataAdapterConfig(_RUN_STARTED_AT),
    )

    # When
    await runtime.aclose()
    await runtime.aclose()

    # Then
    assert len(transport_factory.clients) == 2
    assert all(client.is_closed for client in transport_factory.clients)
    assert [client.close_calls for client in transport_factory.clients] == [1, 1]


@pytest.mark.anyio
async def test_production_collection_closes_transport_once_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    requests: list[httpx2.Request] = []

    def tracked_success(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _success_handler(request)

    transport_factory = _TrackingHttpFactory(tracked_success)
    monkeypatch.setattr(
        client_module,
        "create_async_http_client",
        transport_factory.create,
    )

    # When
    result = await collect_snapshot(
        _request(tmp_path, "lifecycle-first\nlifecycle-first\nlifecycle-second")
    )

    # Then
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert len(transport_factory.clients) == 2
    assert all(client.is_closed for client in transport_factory.clients)
    assert [client.close_calls for client in transport_factory.clients] == [1, 1]
    assert PERSON_IDENTITY_DATASET not in {
        request.url.params.get("dataset_id") for request in requests
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "expected_exit"),
    [
        (
            httpx2.Response(401, json={"error": "classified"}),
            CollectionExitCode.PROVIDER,
        ),
        (
            httpx2.Response(200, json={"invalid": "schema"}),
            CollectionExitCode.INTEGRITY,
        ),
    ],
)
async def test_production_collection_closes_transport_once_on_terminal_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: httpx2.Response,
    expected_exit: CollectionExitCode,
) -> None:
    # Given
    transport_factory = _TrackingHttpFactory(lambda _request: response)
    monkeypatch.setattr(
        client_module,
        "create_async_http_client",
        transport_factory.create,
    )

    # When
    result = await collect_snapshot(_request(tmp_path))

    # Then
    assert result.exit_code is expected_exit
    assert len(transport_factory.clients) == 1
    assert transport_factory.clients[0].is_closed
    assert transport_factory.clients[0].close_calls == 1


@pytest.mark.anyio
async def test_production_collection_shields_transport_close_on_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    request_started = anyio.Event()
    cancellation_observed = anyio.Event()

    async def blocked_handler(_request: httpx2.Request) -> httpx2.Response:
        request_started.set()
        await anyio.sleep_forever()
        raise AssertionError

    transport_factory = _TrackingHttpFactory(blocked_handler)
    monkeypatch.setattr(
        client_module,
        "create_async_http_client",
        transport_factory.create,
    )

    async def run_collection() -> None:
        try:
            _ = await collect_snapshot(_request(tmp_path))
        except anyio.get_cancelled_exc_class():
            cancellation_observed.set()
            raise

    # When
    async with anyio.create_task_group() as group:
        _ = group.start_soon(run_collection)
        await request_started.wait()
        group.cancel_scope.cancel()

    # Then
    assert cancellation_observed.is_set()
    assert len(transport_factory.clients) == 1
    assert transport_factory.clients[0].is_closed
    assert transport_factory.clients[0].close_calls == 1
