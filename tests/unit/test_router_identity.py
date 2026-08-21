from __future__ import annotations

import pytest

from social_media_subscriber.adapters import AdapterOperation
from social_media_subscriber.adapters.instance import AdapterInstanceOrdinal
from social_media_subscriber.adapters.router_outcomes import RouterOperationError
from social_media_subscriber.domain.platform import AccountKind
from tests.fakes.router import make_account
from tests.unit.test_router_support import build_post_requests, build_router


@pytest.mark.anyio
async def test_post_route_rejects_discovery_before_provider_call() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router(((),))

    # When / Then
    with pytest.raises(RouterOperationError):
        _ = await router.route(
            build_post_requests((account,)),
            AdapterOperation.DISCOVER_LOCATOR_POSTS,
        )
    assert factory.calls == []


@pytest.mark.anyio
async def test_post_route_rejects_identity_operation_before_provider_call() -> None:
    # Given
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router(((),))

    # When / Then
    with pytest.raises(RouterOperationError):
        _ = await router.route(
            build_post_requests((account,)),
            AdapterOperation.RESOLVE_ACCOUNT_IDENTITY,
        )
    assert factory.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("keys", "expected_ordinals"),
    [
        (("canary-alpha",), [AdapterInstanceOrdinal(0)]),
        (
            (
                "canary-alpha",
                "canary-alpha",
                "canary-beta",
                "canary-gamma",
                "canary-beta",
            ),
            [
                AdapterInstanceOrdinal(0),
                AdapterInstanceOrdinal(1),
                AdapterInstanceOrdinal(2),
            ],
        ),
    ],
)
async def test_unique_instance_is_created_per_first_seen_credential(
    keys: tuple[str, ...],
    expected_ordinals: list[AdapterInstanceOrdinal],
) -> None:
    # Given / When
    router, factory = build_router(((), (), ()), keys)
    result = await router.route((), AdapterOperation.COLLECT_ACCOUNT_POSTS)

    # Then
    assert factory.created_ordinals == expected_ordinals
    assert "canary" not in repr(result)
    assert "alpha" not in repr(result)
    assert "beta" not in repr(result)
