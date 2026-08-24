from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, final

import pytest
from pydantic import SecretStr, TypeAdapter

from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters.instance import AdapterPostRequest
from social_media_subscriber.bootstrap import bootstrap_runtime
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.platform import Platform
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.providers.apify.models import ApifyPost
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataAdapterConfig,
)
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.runtime_input import RuntimeInput, SourceId, SourceInput
from social_media_subscriber.serialization.json import JsonValue
from social_media_subscriber.storage.merge import merge_snapshot
from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from collections.abc import Sequence

    from social_media_subscriber.providers.apify.requests import ApifyPostInput
    from social_media_subscriber.providers.brightdata.errors import BrightDataError
    from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput

_NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
_PROFILE_URL = "https://www.linkedin.com/in/synthetic-ada/"
_APIFY_FIXTURE = (
    Path(__file__).parents[1] / "fixtures/apify/synthetic-person-original.json"
)
_JSON = TypeAdapter(dict[str, JsonValue])
_MEDIA_ITEMS = TypeAdapter(list[dict[str, JsonValue]])


@final
class ApifyClientFake:
    posts: tuple[ApifyPost, ...]
    failure: ApifyError | None
    calls: int
    close_calls: int

    def __init__(
        self,
        posts: tuple[ApifyPost, ...] = (),
        failure: ApifyError | None = None,
    ) -> None:
        self.posts = posts
        self.failure = failure
        self.calls = 0
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1

    async def collect_posts(self, request: ApifyPostInput) -> tuple[ApifyPost, ...]:
        _ = request
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.posts


@final
class BrightDataClientFake:
    posts: tuple[BrightDataPost, ...]
    failure: BrightDataError | None
    calls: int
    close_calls: int

    def __init__(
        self,
        posts: tuple[BrightDataPost, ...] = (),
        failure: BrightDataError | None = None,
    ) -> None:
        self.posts = posts
        self.failure = failure
        self.calls = 0
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1

    async def collect_person_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        _ = inputs
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.posts

    async def collect_company_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        return await self.collect_person_posts(inputs)


def _account() -> Account:
    locator = parse_linkedin_locator(_PROFILE_URL)
    return Account(
        platform=Platform.LINKEDIN,
        kind=locator.kind,
        profile_url=locator.canonical_url,
        first_seen_at=_NOW,
    )


def _apify_post() -> ApifyPost:
    return ApifyPost.model_validate(_JSON.validate_json(_APIFY_FIXTURE.read_bytes()))


def _brightdata_post() -> BrightDataPost:
    return BrightDataPost.model_validate(
        {
            "id": "urn:li:activity:1001",
            "date_posted": "2026-08-19T09:00:00.000Z",
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/synthetic-ada_example-activity-1001-abcd",
            "profile_url": _PROFILE_URL,
            "post_text": "Synthetic post",
            "images": [
                {
                    "height": 720,
                    "url": "https://media.licdn.com/synthetic-image",
                    "width": 1280,
                }
            ],
        }
    )


def _runtime_input(*source_ids: SourceId) -> RuntimeInput:
    return RuntimeInput(
        locators=(parse_linkedin_locator(_PROFILE_URL),),
        sources=tuple(
            SourceInput(
                source_id=source_id,
                credential=SecretStr(f"synthetic-{index}"),
            )
            for index, source_id in enumerate(source_ids)
        ),
    )


async def _route(
    source_ids: tuple[SourceId, ...],
    apify_clients: tuple[ApifyClientFake, ...],
    brightdata_clients: tuple[BrightDataClientFake, ...],
) -> tuple[SnapshotState, tuple[int, int]]:
    apify = iter(apify_clients)
    brightdata = iter(brightdata_clients)
    runtime = bootstrap_runtime(
        _runtime_input(*source_ids),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: next(brightdata),
        apify_client_builder=lambda _credential: next(apify),
    )
    account = _account()
    try:
        result = await runtime.router.route(
            (AdapterPostRequest(account, date(2023, 1, 1), date(2026, 8, 20)),)
        )
    finally:
        await runtime.aclose()
    return (
        SnapshotState((account,), result.posts),
        (
            sum(client.calls for client in apify_clients),
            sum(client.calls for client in brightdata_clients),
        ),
    )


@pytest.mark.anyio
async def test_apify_retryable_failure_falls_through_to_brightdata() -> None:
    apify = ApifyClientFake(failure=ApifyError(ApifyErrorCategory.RETRYABLE))
    brightdata = BrightDataClientFake((_brightdata_post(),))

    state, calls = await _route(
        (SourceId.APIFY, SourceId.BRIGHTDATA), (apify,), (brightdata,)
    )

    assert calls == (1, 1)
    assert tuple(post.platform_post_id for post in state.posts) == ("1001",)
    assert apify.close_calls == brightdata.close_calls == 1


@pytest.mark.anyio
async def test_apify_is_preferred_even_when_brightdata_is_listed_first() -> None:
    brightdata = BrightDataClientFake((_brightdata_post(),))
    apify = ApifyClientFake((_apify_post(),))

    state, calls = await _route(
        (SourceId.BRIGHTDATA, SourceId.APIFY), (apify,), (brightdata,)
    )

    assert calls == (1, 0)
    assert tuple(post.platform_post_id for post in state.posts) == ("1001",)


@pytest.mark.anyio
async def test_multiple_apify_credentials_create_independent_instances() -> None:
    first = ApifyClientFake(failure=ApifyError(ApifyErrorCategory.RETRYABLE))
    second = ApifyClientFake((_apify_post(),))

    state, calls = await _route((SourceId.APIFY, SourceId.APIFY), (first, second), ())

    assert calls == (2, 0)
    assert tuple(post.platform_post_id for post in state.posts) == ("1001",)
    assert first.close_calls == second.close_calls == 1


@pytest.mark.anyio
async def test_all_apify_credentials_precede_interleaved_brightdata_sources() -> None:
    first_apify = ApifyClientFake(failure=ApifyError(ApifyErrorCategory.RETRYABLE))
    second_apify = ApifyClientFake((_apify_post(),))
    first_brightdata = BrightDataClientFake((_brightdata_post(),))
    second_brightdata = BrightDataClientFake((_brightdata_post(),))

    state, calls = await _route(
        (
            SourceId.BRIGHTDATA,
            SourceId.APIFY,
            SourceId.BRIGHTDATA,
            SourceId.APIFY,
        ),
        (first_apify, second_apify),
        (first_brightdata, second_brightdata),
    )

    assert calls == (2, 0)
    assert tuple(post.platform_post_id for post in state.posts) == ("1001",)


@pytest.mark.anyio
async def test_switching_providers_updates_one_post_instead_of_duplicating() -> None:
    brightdata_state, _ = await _route(
        (SourceId.BRIGHTDATA,), (), (BrightDataClientFake((_brightdata_post(),)),)
    )
    apify_state, _ = await _route(
        (SourceId.APIFY,), (ApifyClientFake((_apify_post(),)),), ()
    )

    merged = merge_snapshot(brightdata_state, apify_state)

    assert len(merged.posts) == 1
    assert merged.posts[0].platform_post_id == "1001"
    assert merged.posts[0].first_seen_at == _NOW


@pytest.mark.anyio
async def test_equivalent_provider_records_have_one_core_without_duplicates() -> None:
    apify_payload = _JSON.validate_json(_APIFY_FIXTURE.read_bytes())
    apify_payload["postedAt"] = {"date": "2026-08-19T09:00:00.035Z"}
    apify_payload["type"] = "post"
    apify_payload["repostId"] = "synthetic-repost"
    apify_payload["linkedinUrl"] = (
        "https://www.linkedin.com/posts/synthetic-ada_apify-route-1001/"
    )
    brightdata = BrightDataPost.model_validate(
        {
            "date_posted": "2026-08-19T09:00:00.019Z",
            "id": "urn:li:activity:1001",
            "images": ["https://media.licdn.com/synthetic-image"],
            "post_text": "Synthetic post",
            "post_type": "post",
            "profile_url": _PROFILE_URL,
            "repost": True,
            "url": "https://www.linkedin.com/posts/synthetic-ada_brightdata-route-1001/",
        }
    )
    apify_state, _ = await _route(
        (SourceId.APIFY,),
        (ApifyClientFake((ApifyPost.model_validate(apify_payload),)),),
        (),
    )
    brightdata_state, _ = await _route(
        (SourceId.BRIGHTDATA,), (), (BrightDataClientFake((brightdata,)),)
    )

    apify_post = apify_state.posts[0]
    brightdata_post = brightdata_state.posts[0]
    merged = merge_snapshot(apify_state, brightdata_state)

    assert apify_post.platform_post_id == brightdata_post.platform_post_id == "1001"
    assert (
        apify_post.canonical_url
        == brightdata_post.canonical_url
        == ("https://www.linkedin.com/feed/update/urn:li:activity:1001/")
    )
    assert (
        apify_post.published_at
        == brightdata_post.published_at
        == datetime(2026, 8, 19, 9, tzinfo=UTC)
    )
    assert apify_post.type == brightdata_post.type == "repost"
    apify_images = _MEDIA_ITEMS.validate_python(apify_post.content["images"])
    brightdata_images = _MEDIA_ITEMS.validate_python(brightdata_post.content["images"])
    assert (
        apify_images[0]["url"]
        == brightdata_images[0]["url"]
        == ("https://media.licdn.com/synthetic-image")
    )
    assert apify_post.content_hash == brightdata_post.content_hash
    assert len(merged.posts) == 1
    assert merged.posts == brightdata_state.posts
