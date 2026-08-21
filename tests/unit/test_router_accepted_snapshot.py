from __future__ import annotations

__test__ = False

import pytest

from social_media_subscriber.adapters.instance import AcceptedSnapshotBatchFailure
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
)
from social_media_subscriber.domain.platform import AccountKind
from tests.fakes.router import CompleteBatch, make_account
from tests.unit.test_router_support import build_post_requests, build_router


@pytest.mark.anyio
async def test_accepted_snapshot_failure_never_retriggers_another_instance() -> None:
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router(
        ((AcceptedSnapshotBatchFailure(),), (CompleteBatch(),))
    )

    result = await router.route(build_post_requests((account,)))

    assert [call.ordinal for call in factory.calls] == [0]
    assert result.accounts == (
        AccountRouteFailed(
            account.id,
            AccountRouteFailureCategory.ACCEPTED_SNAPSHOT_FAILED,
        ),
    )
