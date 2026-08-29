from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from social_media_subscriber.adapters.instance import AdapterInstanceSpec
from social_media_subscriber.adapters.registry import AdapterRegistry
from social_media_subscriber.application.collect import (
    CollectionRequest,
    collect_snapshot,
)
from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
)
from social_media_subscriber.bootstrap import SubscriberRuntime, build_runtime
from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.domain.platform import Platform
from social_media_subscriber.providers.apify.x_models import ApifyXPost
from social_media_subscriber.settings import Settings
from social_media_subscriber.storage.repository import SnapshotRepository
from tests.fakes.router import (
    CompleteBatch,
    FakeDriver,
    ScriptedFactory,
    XFakeDriver,
    make_post,
    make_x_post,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from social_media_subscriber.providers.apify.x_requests import ApifyXPostInput
    from social_media_subscriber.providers.brightdata.models import BrightDataPost
    from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput
    from social_media_subscriber.runtime_input import RuntimeInput

_LINKEDIN_URL = "https://www.linkedin.com/in/lifecycle-person/"
_X_URL = "https://x.com/lifecycle_x/"
_RUN_STARTED_AT = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _request(
    root: Path,
    accounts: str,
    sources: str = "brightdata:synthetic-x-capability",
) -> CollectionRequest:
    return CollectionRequest(
        settings=Settings(
            accounts=SecretStr(accounts),
            sources=SecretStr(sources),
        ),
        previous_snapshot_dir=root / "previous",
        candidate_snapshot_dir=root / "candidate",
        run_started_at=_RUN_STARTED_AT,
    )


@dataclass(slots=True)  # Records forbidden provider calls in tests.
class _NoCallBrightDataClient:
    collect_calls: int = 0
    close_calls: int = 0

    async def aclose(self) -> None:
        self.close_calls += 1

    async def collect_person_posts(
        self,
        inputs: Sequence[PostDiscoveryInput],
    ) -> tuple[BrightDataPost, ...]:
        _ = inputs
        self.collect_calls += 1
        return ()

    async def collect_company_posts(
        self,
        inputs: Sequence[PostDiscoveryInput],
    ) -> tuple[BrightDataPost, ...]:
        _ = inputs
        self.collect_calls += 1
        return ()


@dataclass(slots=True)
class _ApifyXClient:
    collect_calls: int = 0
    close_calls: int = 0
    initial_collection_modes: list[bool] = field(default_factory=list)

    async def aclose(self) -> None:
        self.close_calls += 1

    async def collect_posts(
        self,
        request: ApifyXPostInput,
    ) -> tuple[ApifyXPost, ...]:
        self.collect_calls += 1
        self.initial_collection_modes.append(request.is_initial_collection)
        assert request.profile_url == _X_URL
        return (
            ApifyXPost.model_validate(
                {
                    "author": {"id": "synthetic-author", "username": "lifecycle_x"},
                    "bookmarkCount": 1,
                    "createdAt": "Wed Aug 19 09:00:00 +0000 2026",
                    "id": "2001",
                    "isQuoteStatus": False,
                    "isReply": False,
                    "likeCount": 2,
                    "quoteCount": 3,
                    "replyCount": 4,
                    "retweetCount": 5,
                    "text": "Synthetic X lifecycle post",
                    "type": "tweet",
                    "url": "https://x.com/lifecycle_x/status/2001",
                    "viewCount": 6,
                }
            ),
        )


@pytest.mark.anyio
async def test_collection_accepts_mixed_platform_runtime_and_snapshot(
    tmp_path: Path,
) -> None:
    # Given
    linkedin_factory = ScriptedFactory(
        ((CompleteBatch(((make_post(AccountId(_LINKEDIN_URL), 1),),)),),),
        FakeDriver,
    )
    x_factory = ScriptedFactory(
        ((CompleteBatch(((make_x_post(AccountId(_X_URL), 2),),)),),),
        XFakeDriver,
    )

    def runtime_builder(
        runtime_input: RuntimeInput,
        run_started_at: datetime,
    ) -> SubscriberRuntime:
        _ = runtime_input, run_started_at
        return build_runtime(
            AdapterRegistry((FakeDriver, XFakeDriver)),
            (
                AdapterInstanceSpec(
                    FakeDriver,
                    linkedin_factory,
                    SecretStr("synthetic-linkedin-source"),
                ),
                AdapterInstanceSpec(
                    XFakeDriver,
                    x_factory,
                    SecretStr("synthetic-x-source"),
                ),
            ),
        )

    # When
    result = await collect_snapshot(
        _request(tmp_path, f"{_LINKEDIN_URL}\n{_X_URL}"),
        runtime_builder=runtime_builder,
    )

    # Then
    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert state is not None
    assert tuple(account.profile_url for account in state.accounts) == (
        _LINKEDIN_URL,
        _X_URL,
    )
    assert tuple(post.platform.value for post in state.posts) == ("linkedin", "x")
    assert linkedin_factory.instances[0].close_calls == 1
    assert x_factory.instances[0].close_calls == 1


@pytest.mark.anyio
async def test_production_x_collection_stops_at_unsupported_capability(
    tmp_path: Path,
) -> None:
    # Given
    client = _NoCallBrightDataClient()

    # When
    result = await collect_snapshot(
        _request(tmp_path, _X_URL),
        client_builder=lambda _credential: client,
    )

    # Then
    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.candidate_change is CandidateChange.CHANGED
    assert result.succeeded_accounts == 0
    assert result.failed_accounts == 1
    assert result.failed_account_ids == (AccountId(_X_URL),)
    assert state is not None
    assert state.accounts == ()
    assert state.posts == ()
    assert client.collect_calls == 0
    assert client.close_calls == 1


@pytest.mark.anyio
async def test_production_apify_x_collection_writes_canonical_snapshot(
    tmp_path: Path,
) -> None:
    # Given
    client = _ApifyXClient()

    # When
    result = await collect_snapshot(
        _request(tmp_path, _X_URL, "apify:synthetic-x-capability"),
        apify_x_client_builder=lambda _credential: client,
    )

    # Then
    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert result.candidate_change is CandidateChange.CHANGED
    assert result.succeeded_accounts == 1
    assert result.failed_accounts == 0
    assert state is not None
    assert tuple(account.profile_url for account in state.accounts) == (_X_URL,)
    assert tuple(post.platform_post_id for post in state.posts) == ("2001",)
    assert tuple(post.platform for post in state.posts) == (Platform.X,)
    assert client.collect_calls == 1
    assert client.initial_collection_modes == [True]
    assert client.close_calls == 1


@pytest.mark.anyio
async def test_existing_x_account_uses_incremental_search_lifecycle_mode(
    tmp_path: Path,
) -> None:
    initial_client = _ApifyXClient()
    initial = await collect_snapshot(
        _request(tmp_path, _X_URL, "apify:synthetic-x-capability"),
        apify_x_client_builder=lambda _credential: initial_client,
    )
    incremental_client = _ApifyXClient()
    incremental_request = CollectionRequest(
        settings=Settings(
            accounts=SecretStr(_X_URL),
            sources=SecretStr("apify:synthetic-x-capability"),
        ),
        previous_snapshot_dir=tmp_path / "candidate",
        candidate_snapshot_dir=tmp_path / "incremental",
        run_started_at=_RUN_STARTED_AT,
    )

    incremental = await collect_snapshot(
        incremental_request,
        apify_x_client_builder=lambda _credential: incremental_client,
    )

    assert initial.exit_code is CollectionExitCode.SUCCESS
    assert initial_client.initial_collection_modes == [True]
    assert incremental.exit_code is CollectionExitCode.SUCCESS
    assert incremental_client.initial_collection_modes == [False]
