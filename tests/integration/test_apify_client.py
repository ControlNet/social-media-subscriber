from __future__ import annotations

import json
from datetime import date

import httpx2
import pytest

from social_media_subscriber.providers.apify.client import ApifyClient
from social_media_subscriber.providers.apify.constants import APIFY_ACTOR
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.providers.apify.requests import ApifyPostInput
from social_media_subscriber.providers.http import HttpClientConfig


@pytest.mark.anyio
async def test_client_starts_date_bounded_actor_run_and_downloads_dataset() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == f"/v2/acts/{APIFY_ACTOR}/runs":
            return httpx2.Response(
                201,
                json={
                    "data": {
                        "id": "synthetic-run",
                        "status": "SUCCEEDED",
                        "defaultDatasetId": "synthetic-dataset",
                        "chargedEventCounts": {"no-result": 0},
                    }
                },
            )
        if request.url.path == "/v2/datasets/synthetic-dataset/items":
            return httpx2.Response(
                200,
                json=[
                    {
                        "author": {
                            "linkedinUrl": "https://www.linkedin.com/in/synthetic-ada"
                        },
                        "content": "Synthetic post",
                        "id": "1001",
                        "linkedinUrl": "https://www.linkedin.com/posts/synthetic-ada_example-activity-1001-abcd",
                        "postedAt": {"date": "2026-08-19T09:00:00.000Z"},
                        "type": "post",
                    }
                ],
            )
        raise AssertionError(request.url.path)

    client = ApifyClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    posts = await client.collect_posts(
        ApifyPostInput(
            "https://www.linkedin.com/in/synthetic-ada/",
            date(2023, 1, 2),
            date(2026, 8, 20),
        )
    )

    assert tuple(post.id for post in posts) == ("1001",)
    start = requests[0]
    assert start.headers["authorization"] == "Bearer synthetic-token"
    assert start.url.params["waitForFinish"] == "0"
    assert "maxTotalChargeUsd" not in start.url.params
    assert json.loads(start.content) == {
        "includeQuotePosts": True,
        "includeReposts": True,
        "maxPosts": 0,
        "postNestedComments": False,
        "postNestedReactions": False,
        "postedLimitDate": "2023-01-02",
        "scrapeComments": False,
        "scrapeReactions": False,
        "targetUrls": ["https://www.linkedin.com/in/synthetic-ada/"],
    }
    assert requests[1].url.params["clean"] == "true"
    assert requests[1].url.params["limit"] == "1000"
    assert requests[1].url.params["offset"] == "0"

    await client.aclose()


@pytest.mark.anyio
async def test_explicit_start_rejection_remains_eligible_for_failover() -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429)

    client = ApifyClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(ApifyError) as captured:
        _ = await client.collect_posts(
            ApifyPostInput(
                "https://www.linkedin.com/in/synthetic-ada/",
                date(2023, 1, 2),
                date(2026, 8, 20),
            )
        )

    assert captured.value.category is ApifyErrorCategory.RETRYABLE
    assert captured.value.status == 429
    assert captured.value.run_accepted is False
    await client.aclose()


@pytest.mark.anyio
async def test_ambiguous_start_timeout_prevents_duplicate_provider_run() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        message = "synthetic timeout"
        raise httpx2.ReadTimeout(message, request=request)

    client = ApifyClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(ApifyError) as captured:
        _ = await client.collect_posts(
            ApifyPostInput(
                "https://www.linkedin.com/in/synthetic-ada/",
                date(2023, 1, 2),
                date(2026, 8, 20),
            )
        )

    assert captured.value.category is ApifyErrorCategory.TIMEOUT
    assert captured.value.run_accepted is True
    await client.aclose()


@pytest.mark.anyio
async def test_client_enforces_the_complete_inclusive_date_window() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == f"/v2/acts/{APIFY_ACTOR}/runs":
            return httpx2.Response(
                201,
                json={
                    "data": {
                        "id": "synthetic-run",
                        "status": "SUCCEEDED",
                        "defaultDatasetId": "synthetic-dataset",
                    }
                },
            )
        return httpx2.Response(
            200,
            json=[
                {
                    "author": {
                        "linkedinUrl": ("https://www.linkedin.com/in/synthetic-ada")
                    },
                    "content": f"Synthetic post {post_id}",
                    "id": post_id,
                    "linkedinUrl": (
                        "https://www.linkedin.com/posts/"
                        f"synthetic-ada_example-activity-{post_id}-abcd"
                    ),
                    "postedAt": {"date": posted_at},
                    "type": "post",
                }
                for post_id, posted_at in (
                    ("1000", "2026-08-17T09:00:00.000Z"),
                    ("1001", "2026-08-18T09:00:00.000Z"),
                    ("1002", "2026-08-20T09:00:00.000Z"),
                    ("1003", "2026-08-21T09:00:00.000Z"),
                )
            ],
        )

    client = ApifyClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    posts = await client.collect_posts(
        ApifyPostInput(
            "https://www.linkedin.com/in/synthetic-ada/",
            date(2026, 8, 18),
            date(2026, 8, 20),
        )
    )

    assert tuple(post.id for post in posts) == ("1001", "1002")
    await client.aclose()


@pytest.mark.anyio
async def test_client_downloads_every_dataset_page_without_total_item_limit() -> None:
    offsets: list[str] = []

    def post(post_id: int) -> dict[str, object]:
        return {
            "author": {"linkedinUrl": "https://www.linkedin.com/in/synthetic-ada"},
            "content": f"Synthetic post {post_id}",
            "id": str(post_id),
            "linkedinUrl": (
                "https://www.linkedin.com/posts/"
                f"synthetic-ada_example-activity-{post_id}-abcd"
            ),
            "postedAt": {"date": "2026-08-19T09:00:00.000Z"},
            "type": "post",
        }

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == f"/v2/acts/{APIFY_ACTOR}/runs":
            return httpx2.Response(
                201,
                json={
                    "data": {
                        "id": "synthetic-run",
                        "status": "SUCCEEDED",
                        "defaultDatasetId": "synthetic-dataset",
                    }
                },
            )
        offset = request.url.params["offset"]
        offsets.append(offset)
        start = int(offset)
        stop = 1_000 if start == 0 else 1_002
        return httpx2.Response(200, json=[post(index) for index in range(start, stop)])

    client = ApifyClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    posts = await client.collect_posts(
        ApifyPostInput(
            "https://www.linkedin.com/in/synthetic-ada/",
            date(2026, 8, 18),
            date(2026, 8, 20),
        )
    )

    assert len(posts) == 1_002
    assert offsets == ["0", "1000"]
    await client.aclose()
