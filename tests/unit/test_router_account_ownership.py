from __future__ import annotations

__test__ = False

import pytest

from social_media_subscriber.adapters.instance import BatchCompleted, CollectedAccount
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteSucceeded,
    RouterRunStatus,
)
from social_media_subscriber.domain.platform import AccountKind
from tests.fakes.router import CompleteBatch, make_account, make_post
from tests.unit.test_router_support import build_post_requests, build_router


@pytest.mark.anyio
async def test_duplicate_post_ids_are_idempotent_within_a_result() -> None:
    account = make_account(AccountKind.PERSON, 1)
    post = make_post(account.id, 7)
    router, _factory = build_router(((CompleteBatch(((post, post),)),),))

    result = await router.route(build_post_requests((account,)))

    assert result.posts == (post,)
    assert result.accounts == (AccountRouteSucceeded(account.id, (post.id,)),)


@pytest.mark.anyio
async def test_differing_canonical_post_payload_aborts_all_output() -> None:
    account = make_account(AccountKind.PERSON, 1)
    first = make_post(account.id, 7)
    second = first.model_copy(update={"content": {"text": "changed"}})
    completed = BatchCompleted((CollectedAccount(account.id, (first, second)),))
    router, _factory = build_router(((completed,),))

    result = await router.route(build_post_requests((account,)))

    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.posts == ()


@pytest.mark.anyio
async def test_post_account_ownership_mismatch_aborts_without_output() -> None:
    account = make_account(AccountKind.PERSON, 1)
    other = make_account(AccountKind.PERSON, 2)
    wrong_post = make_post(other.id, 7)
    completed = BatchCompleted((CollectedAccount(account.id, (wrong_post,)),))
    router, _factory = build_router(((completed,),))

    result = await router.route(build_post_requests((account,)))

    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.posts == ()
