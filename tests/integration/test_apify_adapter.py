from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters.instance import (
    AdapterBatch,
    AdapterInstanceOrdinal,
    AdapterPostRequest,
    BatchCompleted,
    CollectedAccount,
    RetryableBatchFailure,
    SchemaBatchFailure,
)
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.platform import Platform
from social_media_subscriber.providers.apify.adapter import (
    ApifyAdapterConfig,
    ApifyLinkedInAdapter,
)
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.providers.apify.models import (
    ApifyAuthor,
    ApifyPost,
    ApifyQuery,
)
from social_media_subscriber.serialization.json import JsonValue

if TYPE_CHECKING:
    from social_media_subscriber.providers.apify.requests import ApifyPostInput

_FIXTURE = Path(__file__).parents[1] / "fixtures/apify/synthetic-person-original.json"
_NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
_PROFILE_URL = "https://www.linkedin.com/in/synthetic-ada/"
_JSON = TypeAdapter(dict[str, JsonValue])


class SyntheticApifyClient:
    posts: tuple[ApifyPost, ...]
    retryable: bool
    close_calls: int

    def __init__(
        self,
        posts: tuple[ApifyPost, ...] = (),
        *,
        retryable: bool = False,
    ) -> None:
        self.posts = posts
        self.retryable = retryable
        self.calls: list[ApifyPostInput] = []
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1

    async def collect_posts(self, request: ApifyPostInput) -> tuple[ApifyPost, ...]:
        self.calls.append(request)
        if self.retryable:
            raise ApifyError(ApifyErrorCategory.RETRYABLE)
        return self.posts


def _account() -> Account:
    locator = parse_linkedin_locator(_PROFILE_URL)
    return Account(
        platform=Platform.LINKEDIN,
        kind=locator.kind,
        profile_url=locator.canonical_url,
        first_seen_at=_NOW,
    )


def _post() -> ApifyPost:
    return ApifyPost.model_validate(_JSON.validate_json(_FIXTURE.read_bytes()))


@pytest.mark.anyio
async def test_apify_normalizes_open_content_into_platform_post() -> None:
    client = SyntheticApifyClient((_post(),))
    account = _account()
    adapter = ApifyLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), ApifyAdapterConfig(_NOW)
    )

    result = await adapter.collect(
        AdapterBatch(
            (AdapterPostRequest(account, date(2026, 8, 17), date(2026, 8, 20)),)
        )
    )

    assert isinstance(result, BatchCompleted)
    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert isinstance(outcome, CollectedAccount)
    assert len(outcome.posts) == 1
    post = outcome.posts[0]
    assert post.platform_post_id == "1001"
    assert post.account_profile_url == _PROFILE_URL
    assert post.canonical_url == (
        "https://www.linkedin.com/feed/update/urn:li:activity:1001/"
    )
    assert post.published_at == datetime(2026, 8, 19, 9, tzinfo=UTC)
    assert post.type == "post"
    assert post.content["text"] == "Synthetic post"
    assert post.content["images"] == [
        {
            "height": 720,
            "url": "https://media.licdn.com/synthetic-image",
            "width": 1280,
        }
    ]
    assert post.content["document"] == {
        "page_count": 2,
        "title": "Synthetic document",
    }
    assert post.content["engagement"] == {
        "comments": 2,
        "likes": 7,
        "shares": 1,
    }
    assert post.content["author"] == {
        "name": "Synthetic Ada",
        "profile_url": (
            "https://www.linkedin.com/in/synthetic-ada?miniProfileUrn=synthetic"
        ),
        "publicIdentifier": "synthetic-ada",
        "type": "profile",
    }
    assert client.calls[0].profile_url == _PROFILE_URL
    assert client.calls[0].start_date == date(2026, 8, 17)
    assert client.calls[0].end_date == date(2026, 8, 20)


@pytest.mark.anyio
async def test_apify_retryable_failure_remains_eligible_for_router_failover() -> None:
    client = SyntheticApifyClient(retryable=True)
    account = _account()
    adapter = ApifyLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), ApifyAdapterConfig(_NOW)
    )

    result = await adapter.collect(
        AdapterBatch(
            (AdapterPostRequest(account, date(2026, 8, 17), date(2026, 8, 20)),)
        )
    )

    assert isinstance(result, RetryableBatchFailure)


@pytest.mark.anyio
async def test_apify_uses_query_target_for_repost_ownership() -> None:
    repost = _post().model_copy(
        update={
            "author": ApifyAuthor.model_validate(
                {"linkedinUrl": "https://www.linkedin.com/in/synthetic-original/"}
            )
        }
    )
    client = SyntheticApifyClient((repost,))
    account = _account()
    adapter = ApifyLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), ApifyAdapterConfig(_NOW)
    )

    result = await adapter.collect(
        AdapterBatch(
            (AdapterPostRequest(account, date(2026, 8, 17), date(2026, 8, 20)),)
        )
    )

    assert isinstance(result, BatchCompleted)


@pytest.mark.anyio
async def test_apify_rejects_query_target_for_another_account() -> None:
    wrong_target = _post().model_copy(
        update={
            "query": ApifyQuery.model_validate(
                {"targetUrl": "https://www.linkedin.com/in/synthetic-other/"}
            )
        }
    )
    client = SyntheticApifyClient((wrong_target,))
    account = _account()
    adapter = ApifyLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), ApifyAdapterConfig(_NOW)
    )

    result = await adapter.collect(
        AdapterBatch(
            (AdapterPostRequest(account, date(2026, 8, 17), date(2026, 8, 20)),)
        )
    )

    assert isinstance(result, SchemaBatchFailure)
