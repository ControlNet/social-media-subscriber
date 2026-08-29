from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, final

import pytest

from social_media_subscriber.accounts.locator import parse_x_locator
from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    AdapterBatch,
    AdapterInstanceOrdinal,
    AdapterPostRequest,
    BatchCompleted,
    CollectedAccount,
)
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.platform import Platform
from social_media_subscriber.providers.apify.adapter import ApifyAdapterConfig
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.providers.apify.x_adapter import ApifyXAdapter
from social_media_subscriber.providers.apify.x_models import ApifyXPost

if TYPE_CHECKING:
    from social_media_subscriber.providers.apify.x_requests import ApifyXPostInput

_FIXTURE = Path(__file__).parents[1] / "fixtures/apify/synthetic-x-original.json"
_NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
_PROFILE_URL = "https://x.com/synthetic_ada/"


@final
class SyntheticApifyXClient:
    posts: tuple[ApifyXPost, ...]
    failure: ApifyError | None
    close_calls: int
    requests: list[ApifyXPostInput]

    def __init__(
        self,
        posts: tuple[ApifyXPost, ...] = (),
        failure: ApifyError | None = None,
    ) -> None:
        self.posts = posts
        self.failure = failure
        self.close_calls = 0
        self.requests = []

    async def aclose(self) -> None:
        self.close_calls += 1

    async def collect_posts(
        self,
        request: ApifyXPostInput,
    ) -> tuple[ApifyXPost, ...]:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return self.posts


def _account() -> Account:
    locator = parse_x_locator(_PROFILE_URL)
    return Account(
        platform=Platform.X,
        kind=locator.kind,
        profile_url=locator.canonical_url,
        first_seen_at=_NOW,
    )


def _batch(*, is_initial_collection: bool = False) -> AdapterBatch:
    return AdapterBatch(
        (
            AdapterPostRequest(
                _account(),
                date(2026, 8, 17),
                date(2026, 8, 20),
                is_initial_collection=is_initial_collection,
            ),
        )
    )


@pytest.mark.anyio
async def test_x_adapter_normalizes_real_tweet_for_profile_account() -> None:
    client = SyntheticApifyXClient(
        (ApifyXPost.model_validate_json(_FIXTURE.read_bytes()),)
    )
    adapter = ApifyXAdapter(client, AdapterInstanceOrdinal(0), ApifyAdapterConfig(_NOW))

    result = await adapter.collect(_batch())

    assert isinstance(result, BatchCompleted)
    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert isinstance(outcome, CollectedAccount)
    assert outcome.account_id == _account().id
    assert tuple(post.platform_post_id for post in outcome.posts) == ("2001",)
    assert outcome.posts[0].platform is Platform.X
    assert client.requests[0].is_initial_collection is False
    await adapter.aclose()
    assert client.close_calls == 1


@pytest.mark.anyio
async def test_x_adapter_forwards_initial_collection_mode() -> None:
    client = SyntheticApifyXClient()
    adapter = ApifyXAdapter(client, AdapterInstanceOrdinal(0), ApifyAdapterConfig(_NOW))

    result = await adapter.collect(_batch(is_initial_collection=True))

    assert isinstance(result, BatchCompleted)
    assert client.requests[0].is_initial_collection is True


@pytest.mark.anyio
async def test_x_adapter_treats_incomplete_paid_run_as_accepted_failure() -> None:
    client = SyntheticApifyXClient(
        failure=ApifyError(ApifyErrorCategory.INCOMPLETE, run_accepted=True)
    )
    adapter = ApifyXAdapter(client, AdapterInstanceOrdinal(0), ApifyAdapterConfig(_NOW))

    result = await adapter.collect(_batch())

    assert isinstance(result, AcceptedSnapshotBatchFailure)
