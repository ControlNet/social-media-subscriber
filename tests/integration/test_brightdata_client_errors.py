from __future__ import annotations

import json

import pytest
from structlog.testing import capture_logs

from social_media_subscriber.providers.brightdata.client import BrightDataClient
from social_media_subscriber.providers.brightdata.constants import (
    PERSON_IDENTITY_DATASET,
)
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.providers.http import HttpClientConfig
from tests.integration.test_brightdata_client_requests import (
    JsonValue,
    RecordedRequest,
    ResponsePayload,
    fake_server,
)


@pytest.mark.anyio
async def test_person_identity_uses_exact_sync_scrape_contract() -> None:
    # Given
    urls = ("https://www.linkedin.com/in/example/",)
    response: JsonValue = {"linkedin_num_id": "123", "url": urls[0]}
    async with fake_server([(200, response)]) as (state, base_url):
        client = BrightDataClient("test-secret", HttpClientConfig(base_url=base_url))

        # When
        async with client:
            result = await client.resolve_person_identities(urls)

    # Then
    assert len(result) == 1
    assert state.requests == [
        RecordedRequest(
            method="POST",
            target=(
                "/datasets/v3/scrape?dataset_id="
                f"{PERSON_IDENTITY_DATASET}&notify=false&include_errors=true"
            ),
            authorization_present=True,
            body={
                "input": [{"url": url} for url in urls],
                "limit_per_input": None,
            },
        )
    ]


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
    # Given
    async with fake_server([(status, {"error": "provider-text-canary"})]) as (
        _state,
        base_url,
    ):
        client = BrightDataClient(
            "credential-canary", HttpClientConfig(base_url=base_url)
        )

        # When / Then
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.resolve_person_identities(
                    ("https://www.linkedin.com/in/example/",)
                )

    assert captured.value.category is category
    assert "canary" not in str(captured.value)
    assert "canary" not in repr(captured.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        {"unexpected": "envelope"},
        [{"linkedin_num_id": 123}],
        "malformed-envelope",
    ],
)
async def test_schema_failures_are_typed_and_redacted(response: JsonValue) -> None:
    # Given
    async with fake_server([(200, response)]) as (_state, base_url):
        client = BrightDataClient(
            "credential-canary", HttpClientConfig(base_url=base_url)
        )

        # When / Then
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.resolve_person_identities(
                    ("https://www.linkedin.com/in/example/",)
                )

    assert captured.value.category is BrightDataErrorCategory.SCHEMA
    assert "canary" not in str(captured.value)


@pytest.mark.anyio
async def test_unknown_snapshot_status_is_terminal_schema_failure() -> None:
    # Given
    async def sleeper(_delay: float) -> None:
        return

    responses: list[tuple[int, ResponsePayload]] = [
        (200, {"snapshot_id": "safe-id"}),
        (200, {"status": "provider-injected-status"}),
    ]
    async with fake_server(responses) as (state, base_url):
        client = BrightDataClient(
            "credential-canary",
            HttpClientConfig(base_url=base_url),
            sleeper=sleeper,
        )

        # When / Then
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.resolve_person_identities(
                    ("https://www.linkedin.com/in/example/",)
                )

    assert captured.value.category is BrightDataErrorCategory.SCHEMA
    assert len(state.requests) == 2


@pytest.mark.anyio
async def test_terminal_snapshot_status_is_distinct_and_never_downloads() -> None:
    # Given
    async def sleeper(_delay: float) -> None:
        return

    responses: list[tuple[int, ResponsePayload]] = [
        (200, {"snapshot_id": "safe-id"}),
        (200, {"status": "failed"}),
    ]
    async with fake_server(responses) as (state, base_url):
        client = BrightDataClient(
            "credential-canary",
            HttpClientConfig(base_url=base_url),
            sleeper=sleeper,
        )

        # When / Then
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.resolve_person_identities(
                    ("https://www.linkedin.com/in/example/",)
                )

    assert captured.value.category is BrightDataErrorCategory.SNAPSHOT_TERMINAL
    assert captured.value.snapshot_accepted
    assert len(state.requests) == 2


@pytest.mark.anyio
async def test_malformed_json_is_a_sanitized_schema_failure() -> None:
    # Given
    async with fake_server([(200, b'{"provider-canary":')]) as (_state, base_url):
        client = BrightDataClient(
            "credential-canary", HttpClientConfig(base_url=base_url)
        )

        # When / Then
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.resolve_person_identities(
                    ("https://www.linkedin.com/in/example/",)
                )

    assert captured.value.category is BrightDataErrorCategory.SCHEMA
    assert "canary" not in str(captured.value)


@pytest.mark.anyio
async def test_include_error_record_is_input_failure_not_identity() -> None:
    # Given
    async with fake_server(
        [(200, {"error": "provider-canary", "input": {"url": "url-canary"}})]
    ) as (_state, base_url):
        client = BrightDataClient(
            "credential-canary", HttpClientConfig(base_url=base_url)
        )

        # When / Then
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.resolve_person_identities(
                    ("https://www.linkedin.com/in/example/",)
                )

    assert captured.value.category is BrightDataErrorCategory.INPUT
    assert "canary" not in str(captured.value)


@pytest.mark.anyio
async def test_snapshot_poll_failure_retains_accepted_ownership() -> None:
    # Given
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

        # When / Then
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.resolve_person_identities(
                    ("https://www.linkedin.com/in/example/",)
                )

    assert captured.value.category is BrightDataErrorCategory.AUTH
    assert captured.value.snapshot_accepted
    assert sum(request.method == "POST" for request in state.requests) == 1


@pytest.mark.anyio
async def test_logs_never_contain_credentials_urls_or_provider_text() -> None:
    # Given
    url_canary = "https://www.linkedin.com/in/url-canary/"
    with capture_logs() as logs:
        async with fake_server([(401, {"error": "provider-canary"})]) as (
            _state,
            base_url,
        ):
            client = BrightDataClient(
                "credential-canary", HttpClientConfig(base_url=base_url)
            )

            # When
            async with client:
                with pytest.raises(BrightDataError):
                    _ = await client.resolve_person_identities((url_canary,))

    # Then
    rendered = json.dumps(logs)
    assert "credential-canary" not in rendered
    assert "url-canary" not in rendered
    assert "provider-canary" not in rendered
    assert all("headers" not in event and "body" not in event for event in logs)


@pytest.mark.anyio
async def test_snapshot_poll_timeout_is_distinct_and_does_not_retrigger() -> None:
    # Given
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

        # When / Then
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.resolve_person_identities(
                    ("https://www.linkedin.com/in/example/",)
                )

    assert captured.value.category is BrightDataErrorCategory.SNAPSHOT_TIMEOUT
    assert sleep_calls == 60
    assert sum(request.method == "POST" for request in state.requests) == 1


@pytest.mark.anyio
async def test_batch_bounds_reject_zero_and_twenty_one_without_io() -> None:
    # Given
    async with fake_server([]) as (state, base_url):
        client = BrightDataClient("test-secret", HttpClientConfig(base_url=base_url))

        # When / Then
        async with client:
            with pytest.raises(BrightDataError) as empty:
                _ = await client.resolve_person_identities(())
            with pytest.raises(BrightDataError) as oversized:
                _ = await client.resolve_person_identities(
                    tuple("x" for _ in range(21))
                )

    assert empty.value.category is BrightDataErrorCategory.INPUT
    assert oversized.value.category is BrightDataErrorCategory.INPUT
    assert state.requests == []
