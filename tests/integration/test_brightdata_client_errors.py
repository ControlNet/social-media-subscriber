from __future__ import annotations

import json
from datetime import date

import pytest
from structlog.testing import capture_logs

from social_media_subscriber.providers.brightdata.client import BrightDataClient
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput
from social_media_subscriber.providers.http import HttpClientConfig
from tests.integration.test_brightdata_client_requests import (
    JsonValue,
    ResponsePayload,
    fake_server,
)


def _input(url: str = "https://www.linkedin.com/in/example/") -> PostDiscoveryInput:
    return PostDiscoveryInput(url, date(2026, 8, 19), date(2026, 8, 20))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "category"),
    [
        (400, BrightDataErrorCategory.INPUT),
        (401, BrightDataErrorCategory.AUTH),
        (402, BrightDataErrorCategory.QUOTA),
        (404, BrightDataErrorCategory.NOT_FOUND),
        (422, BrightDataErrorCategory.INPUT),
    ],
)
async def test_http_failures_map_without_provider_text(
    status: int,
    category: BrightDataErrorCategory,
) -> None:
    async with fake_server([(status, {"error": "provider-text-canary"})]) as (
        _state,
        base_url,
    ):
        client = BrightDataClient(
            "credential-canary", HttpClientConfig(base_url=base_url)
        )
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.collect_person_posts((_input(),))

    assert captured.value.category is category
    assert "canary" not in str(captured.value)
    assert "canary" not in repr(captured.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        {"unexpected": "envelope"},
        [{"id": 123}],
        "malformed-envelope",
    ],
)
async def test_schema_failures_are_typed_and_redacted(response: JsonValue) -> None:
    async with fake_server([(200, response)]) as (_state, base_url):
        client = BrightDataClient(
            "credential-canary", HttpClientConfig(base_url=base_url)
        )
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.collect_person_posts((_input(),))

    assert captured.value.category is BrightDataErrorCategory.SCHEMA
    assert "canary" not in str(captured.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_status", "category"),
    [
        ("provider-injected-status", BrightDataErrorCategory.SCHEMA),
        ("failed", BrightDataErrorCategory.SNAPSHOT_TERMINAL),
    ],
)
async def test_terminal_snapshot_status_never_downloads(
    provider_status: str,
    category: BrightDataErrorCategory,
) -> None:
    async def sleeper(_delay: float) -> None:
        return

    responses: list[tuple[int, ResponsePayload]] = [
        (200, {"snapshot_id": "safe-id"}),
        (200, {"status": provider_status}),
    ]
    async with fake_server(responses) as (state, base_url):
        client = BrightDataClient(
            "credential-canary",
            HttpClientConfig(base_url=base_url),
            sleeper=sleeper,
        )
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.collect_person_posts((_input(),))

    assert captured.value.category is category
    assert captured.value.snapshot_accepted
    assert len(state.requests) == 2


@pytest.mark.anyio
async def test_malformed_json_is_a_sanitized_schema_failure() -> None:
    async with fake_server([(200, b'{"provider-canary":')]) as (_state, base_url):
        client = BrightDataClient(
            "credential-canary", HttpClientConfig(base_url=base_url)
        )
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.collect_person_posts((_input(),))

    assert captured.value.category is BrightDataErrorCategory.SCHEMA
    assert "canary" not in str(captured.value)


@pytest.mark.anyio
async def test_include_error_record_is_typed_input_failure() -> None:
    async with fake_server(
        [(200, {"error": "provider-canary", "input": {"url": "url-canary"}})]
    ) as (_state, base_url):
        client = BrightDataClient(
            "credential-canary", HttpClientConfig(base_url=base_url)
        )
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.collect_person_posts((_input(),))

    assert captured.value.category is BrightDataErrorCategory.INPUT
    assert "canary" not in str(captured.value)


@pytest.mark.anyio
async def test_snapshot_poll_failure_retains_accepted_ownership() -> None:
    async def sleeper(_delay: float) -> None:
        return

    responses: list[tuple[int, ResponsePayload]] = [
        (200, {"snapshot_id": "safe-id"}),
        (401, {"error": "provider-canary"}),
    ]
    async with fake_server(responses) as (state, base_url):
        client = BrightDataClient(
            "credential-canary",
            HttpClientConfig(base_url=base_url),
            sleeper=sleeper,
        )
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.collect_person_posts((_input(),))

    assert captured.value.category is BrightDataErrorCategory.AUTH
    assert captured.value.snapshot_accepted
    assert sum(request.method == "POST" for request in state.requests) == 1


@pytest.mark.anyio
async def test_logs_never_contain_credentials_urls_or_provider_text() -> None:
    with capture_logs() as logs:
        async with fake_server([(401, {"error": "provider-canary"})]) as (
            _state,
            base_url,
        ):
            client = BrightDataClient(
                "credential-canary", HttpClientConfig(base_url=base_url)
            )
            async with client:
                with pytest.raises(BrightDataError):
                    _ = await client.collect_person_posts(
                        (_input("https://www.linkedin.com/in/url-canary/"),)
                    )

    rendered = json.dumps(logs)
    assert "credential-canary" not in rendered
    assert "url-canary" not in rendered
    assert "provider-canary" not in rendered
    assert all("headers" not in event and "body" not in event for event in logs)


@pytest.mark.anyio
async def test_snapshot_poll_timeout_is_distinct_and_does_not_retrigger() -> None:
    sleep_calls = 0

    async def sleeper(_delay: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    responses: list[tuple[int, ResponsePayload]] = [(200, {"snapshot_id": "safe-id"})]
    responses.extend((200, {"status": "running"}) for _ in range(60))
    async with fake_server(responses) as (state, base_url):
        client = BrightDataClient(
            "credential-canary",
            HttpClientConfig(base_url=base_url),
            sleeper=sleeper,
        )
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.collect_person_posts((_input(),))

    assert captured.value.category is BrightDataErrorCategory.SNAPSHOT_TIMEOUT
    assert sleep_calls == 60
    assert sum(request.method == "POST" for request in state.requests) == 1


@pytest.mark.anyio
async def test_batch_bounds_reject_zero_and_twenty_one_without_io() -> None:
    async with fake_server([]) as (state, base_url):
        client = BrightDataClient("test-secret", HttpClientConfig(base_url=base_url))
        async with client:
            with pytest.raises(BrightDataError) as empty:
                _ = await client.collect_person_posts(())
            with pytest.raises(BrightDataError) as oversized:
                _ = await client.collect_person_posts(
                    tuple(_input() for _ in range(21))
                )

    assert empty.value.category is BrightDataErrorCategory.INPUT
    assert oversized.value.category is BrightDataErrorCategory.INPUT
    assert state.requests == []
