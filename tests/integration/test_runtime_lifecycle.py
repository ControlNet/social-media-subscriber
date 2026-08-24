from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

import anyio
import httpx2
import pytest
from pydantic import SecretStr

from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters.instance import AdapterInstanceSpec
from social_media_subscriber.adapters.registry import AdapterRegistry
from social_media_subscriber.application.collect import (
    CollectionRequest,
    collect_snapshot,
)
from social_media_subscriber.application.results import CollectionExitCode
from social_media_subscriber.bootstrap import bootstrap_runtime, build_runtime
from social_media_subscriber.providers.brightdata import client as client_module
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataAdapterConfig,
)
from social_media_subscriber.providers.http import HttpClientConfig
from social_media_subscriber.runtime_input import (
    RuntimeInput,
    SourceId,
    SourceInput,
    load_runtime_input,
)
from social_media_subscriber.settings import Settings
from tests.fakes.router import FakeDriver, ScriptedFactory

if TYPE_CHECKING:
    from pathlib import Path

    from social_media_subscriber.bootstrap import SubscriberRuntime


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


def _request(
    root: Path, keys: str = "lifecycle-first\nlifecycle-second"
) -> CollectionRequest:
    return CollectionRequest(
        settings=Settings(
            accounts=SecretStr(_PERSON_URL),
            sources=SecretStr(
                "\n".join(
                    f"brightdata:{key.strip()}"
                    for key in keys.splitlines()
                    if key.strip()
                )
            ),
        ),
        previous_snapshot_dir=root / "previous",
        candidate_snapshot_dir=root / "candidate",
        run_started_at=_RUN_STARTED_AT,
    )


def _success_handler(request: httpx2.Request) -> httpx2.Response:
    _ = request
    payload = [
        {
            "id": "activity-1",
            "date_posted": "2026-08-18T12:00:00+00:00",
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/activity-1/",
            "user_id": "synthetic-provider-user",
            "profile_url": _PERSON_URL,
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
        RuntimeInput(
            locators=(parse_linkedin_locator(_PERSON_URL),),
            sources=(
                SourceInput(
                    source_id=SourceId.BRIGHTDATA,
                    credential=SecretStr("lifecycle-first"),
                ),
                SourceInput(
                    source_id=SourceId.BRIGHTDATA,
                    credential=SecretStr("lifecycle-second"),
                ),
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
async def test_bootstrap_preserves_source_order_after_exact_line_deduplication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    transport_factory = _TrackingHttpFactory(_success_handler)
    monkeypatch.setattr(
        client_module,
        "create_async_http_client",
        transport_factory.create,
    )
    source_lines = (
        "brightdata:lifecycle-first",
        "brightdata:lifecycle-first",
        "brightdata:lifecycle-second",
    )
    runtime_input = load_runtime_input(
        Settings(
            accounts=SecretStr(_PERSON_URL),
            sources=SecretStr("\n".join(source_lines)),
        )
    )
    created_credentials: list[str] = []

    def client_builder(credential: str) -> client_module.BrightDataClient:
        created_credentials.append(credential)
        return client_module.BrightDataClient(credential)

    # When
    runtime = bootstrap_runtime(
        runtime_input,
        BrightDataAdapterConfig(_RUN_STARTED_AT),
        client_builder=client_builder,
    )
    await runtime.aclose()

    # Then
    assert len(runtime_input.sources) == 2
    assert created_credentials == ["lifecycle-first", "lifecycle-second"]
    assert len(transport_factory.clients) == 2
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
    assert requests


@pytest.mark.anyio
async def test_collection_accepts_and_closes_provider_neutral_runtime(
    tmp_path: Path,
) -> None:
    # Given
    factory = ScriptedFactory(((),))
    observed_inputs: list[tuple[RuntimeInput, datetime]] = []

    def runtime_builder(
        runtime_input: RuntimeInput,
        run_started_at: datetime,
    ) -> SubscriberRuntime:
        observed_inputs.append((runtime_input, run_started_at))
        return build_runtime(
            AdapterRegistry((FakeDriver,)),
            (
                AdapterInstanceSpec(
                    FakeDriver,
                    factory,
                    SecretStr("alternate-provider-key"),
                ),
            ),
        )

    # When
    result = await collect_snapshot(
        _request(tmp_path),
        runtime_builder=runtime_builder,
    )

    # Then
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert observed_inputs[0][1] == _RUN_STARTED_AT
    assert tuple(
        locator.canonical_url for locator in observed_inputs[0][0].locators
    ) == (_PERSON_URL,)
    assert factory.created_ordinals == [0]
    assert factory.instances[0].close_calls == 1


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
    assert len(transport_factory.clients) == 2
    assert all(client.is_closed for client in transport_factory.clients)
    assert [client.close_calls for client in transport_factory.clients] == [1, 1]


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
    assert len(transport_factory.clients) == 2
    assert all(client.is_closed for client in transport_factory.clients)
    assert [client.close_calls for client in transport_factory.clients] == [1, 1]
