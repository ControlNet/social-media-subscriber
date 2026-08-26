from __future__ import annotations

from dataclasses import dataclass
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

    from social_media_subscriber.providers.brightdata.models import BrightDataPost
    from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput
    from social_media_subscriber.runtime_input import RuntimeInput

_LINKEDIN_URL = "https://www.linkedin.com/in/lifecycle-person/"
_X_URL = "https://x.com/lifecycle_x/"
_RUN_STARTED_AT = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _request(root: Path, accounts: str) -> CollectionRequest:
    return CollectionRequest(
        settings=Settings(
            accounts=SecretStr(accounts),
            sources=SecretStr("brightdata:synthetic-x-capability"),
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
