from __future__ import annotations

import pytest

from social_media_subscriber.adapters import AdapterOperation
from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    LocatorPostsBatchCompleted,
)
from social_media_subscriber.adapters.router_discovery_state import (
    DiscoveryFailureCategory,
    DiscoveryLocatorFailed,
)
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
)
from social_media_subscriber.domain.platform import AccountKind
from tests.fakes.router import CompleteBatch, make_account
from tests.unit.test_router_support import (
    build_locator_request,
    build_post_requests,
    build_resolved_locator,
    build_router,
)


@pytest.mark.anyio
async def test_accepted_snapshot_failure_never_retriggers_another_instance() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router(
        ((AcceptedSnapshotBatchFailure(),), (CompleteBatch(),))
    )

    # When
    result = await router.route(
        build_post_requests((account,)), AdapterOperation.COLLECT_ACCOUNT_POSTS
    )

    # Then
    assert [call.ordinal for call in factory.calls] == [0]
    assert result.accounts == (
        AccountRouteFailed(
            account.id,
            AccountRouteFailureCategory.ACCEPTED_SNAPSHOT_FAILED,
        ),
    )


@pytest.mark.anyio
async def test_locator_discovery_accepted_snapshot_is_terminal_no_reroute() -> None:
    # Given
    request = build_locator_request(AccountKind.PERSON, 1)
    account = make_account(AccountKind.PERSON, 1)
    completed = LocatorPostsBatchCompleted((build_resolved_locator(request, account),))
    router, factory = build_router(
        ((), ()),
        locator_scripts=((AcceptedSnapshotBatchFailure(),), (completed,)),
    )

    # When
    result = await router.discover_posts((request,))

    # Then
    assert [call.ordinal for call in factory.locator_calls] == [0]
    outcome = result.outcomes[0]
    assert isinstance(outcome, DiscoveryLocatorFailed)
    assert outcome.category is DiscoveryFailureCategory.ACCEPTED_SNAPSHOT_FAILED
    assert result.accounts == ()
