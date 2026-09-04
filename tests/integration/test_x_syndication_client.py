from __future__ import annotations

import httpx2
import pytest

from social_media_subscriber.providers.x_syndication import (
    SyndicationClientConfig,
    SyndicationMissCategory,
    XMediaSyndicationClient,
)


def _payload() -> dict[str, object]:
    return {
        "id_str": "9001",
        "created_at": "2026-06-06T17:29:01.000Z",
        "text": "Referenced post",
        "user": {
            "name": "Referenced User",
            "screen_name": "referenced_user",
            "profile_image_url_https": "https://pbs.twimg.com/avatar.jpg",
        },
        "mediaDetails": [],
    }


@pytest.mark.anyio
async def test_client_uses_fixed_query_without_authorization() -> None:
    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_payload())

    client = XMediaSyndicationClient(
        SyndicationClientConfig(base_url="https://cdn.syndication.twimg.com"),
        transport=httpx2.MockTransport(handler),
    )

    async with client:
        result = await client.fetch("9001")

    assert result.tweet is not None
    assert result.miss_category is None
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/tweet-result"
    assert dict(request.url.params) == {"id": "9001", "lang": "en", "token": "1"}
    assert "authorization" not in request.headers


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (403, SyndicationMissCategory.FORBIDDEN),
        (404, SyndicationMissCategory.NOT_FOUND),
        (429, SyndicationMissCategory.RATE_LIMIT),
        (503, SyndicationMissCategory.SERVER),
    ],
)
async def test_client_maps_http_failures_to_safe_categories(
    status: int, expected: SyndicationMissCategory
) -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, content=b"private-provider-body")

    client = XMediaSyndicationClient(transport=httpx2.MockTransport(handler))

    async with client:
        result = await client.fetch("9001")

    assert result.tweet is None
    assert result.miss_category is expected


@pytest.mark.anyio
async def test_client_maps_invalid_json_and_schema_without_exposing_body() -> None:
    responses = iter(
        (
            httpx2.Response(200, content=b"private-invalid-json"),
            httpx2.Response(200, json={"text": "private-invalid-schema"}),
        )
    )

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return next(responses)

    client = XMediaSyndicationClient(transport=httpx2.MockTransport(handler))

    async with client:
        invalid_json = await client.fetch("9001")
        invalid_schema = await client.fetch("9002")

    assert invalid_json.miss_category is SyndicationMissCategory.SCHEMA
    assert invalid_schema.miss_category is SyndicationMissCategory.SCHEMA
    assert "private-invalid-json" not in repr(invalid_json)
    assert "private-invalid-schema" not in repr(invalid_schema)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            httpx2.ReadTimeout("private-timeout"),
            SyndicationMissCategory.TIMEOUT,
        ),
        (
            httpx2.ConnectError("private-network-error"),
            SyndicationMissCategory.NETWORK,
        ),
    ],
)
async def test_client_maps_transport_failures_without_exposing_details(
    error: httpx2.RequestError,
    expected: SyndicationMissCategory,
) -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        raise error

    client = XMediaSyndicationClient(transport=httpx2.MockTransport(handler))

    async with client:
        result = await client.fetch("9001")

    assert result.tweet is None
    assert result.miss_category is expected
    assert str(error) not in repr(result)
