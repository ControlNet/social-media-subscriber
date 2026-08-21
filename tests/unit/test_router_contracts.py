from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters import (
    AdapterOperation,
    AdapterRegistry,
    ResolvedAdapterDrivers,
    adapter,
)
from social_media_subscriber.adapters import instance as instance_contract
from social_media_subscriber.adapters.instance import CollectedAccount
from social_media_subscriber.application import windows as window_contract
from social_media_subscriber.application.windows import ExplicitWindow, WindowContext
from social_media_subscriber.domain.platform import AccountKind, Platform
from tests.fakes.router import DeclaredFakeDriver, FakeDriver, make_account, make_post
from tests.unit.test_router_support import build_router


def test_locator_operation_contract_is_closed() -> None:
    # Given / When
    values = tuple(operation.value for operation in AdapterOperation)

    # Then
    assert values == (
        "resolve_account_identity",
        "collect_account_posts",
        "discover_locator_posts",
    )


def test_locator_batch_deduplicates_canonical_requests_and_rejects_mixed_kinds() -> (
    None
):
    # Given
    assert hasattr(instance_contract, "AdapterPostLocatorRequest")
    assert hasattr(instance_contract, "AdapterPostLocatorBatch")
    person = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    duplicate = parse_linkedin_locator("https://linkedin.com/in/example-person")
    company = parse_linkedin_locator(
        "https://www.linkedin.com/company/example-company/"
    )
    request = instance_contract.AdapterPostLocatorRequest(
        person, date(2026, 8, 14), date(2026, 8, 21)
    )
    equivalent = instance_contract.AdapterPostLocatorRequest(
        duplicate, date(2026, 8, 14), date(2026, 8, 21)
    )

    # When
    batch = instance_contract.AdapterPostLocatorBatch((request, equivalent))

    # Then
    assert batch.requests == (request,)
    with pytest.raises(instance_contract.AdapterRequestError) as mixed:
        _ = instance_contract.AdapterPostLocatorBatch(
            (
                request,
                instance_contract.AdapterPostLocatorRequest(
                    company, date(2026, 8, 14), date(2026, 8, 21)
                ),
            )
        )
    assert (
        mixed.value.category
        is instance_contract.AdapterRequestErrorCategory.MIXED_LOCATOR_KIND
    )


def test_locator_batch_rejects_conflicting_duplicate_windows_before_fake_calls() -> (
    None
):
    # Given
    assert hasattr(instance_contract, "AdapterPostLocatorRequest")
    assert hasattr(instance_contract, "AdapterPostLocatorBatch")
    locator = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    first = instance_contract.AdapterPostLocatorRequest(
        locator, date(2026, 8, 14), date(2026, 8, 21)
    )
    conflicting = instance_contract.AdapterPostLocatorRequest(
        locator, date(2026, 8, 15), date(2026, 8, 21)
    )
    _, factory = build_router(((),))

    # When / Then
    with pytest.raises(instance_contract.AdapterRequestError) as captured:
        _ = instance_contract.AdapterPostLocatorBatch((first, conflicting))
    assert (
        captured.value.category
        is instance_contract.AdapterRequestErrorCategory.CONFLICTING_WINDOW
    )
    assert factory.calls == []


def test_locator_request_rejects_inverted_window_before_fake_calls() -> None:
    # Given
    assert hasattr(instance_contract, "AdapterPostLocatorRequest")
    locator = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    _, factory = build_router(((),))

    # When / Then
    with pytest.raises(instance_contract.AdapterRequestError) as captured:
        _ = instance_contract.AdapterPostLocatorRequest(
            locator, date(2026, 8, 22), date(2026, 8, 21)
        )
    assert (
        captured.value.category
        is instance_contract.AdapterRequestErrorCategory.INVERTED_WINDOW
    )
    assert factory.calls == []


def test_locator_window_uses_initial_policy_without_reading_prior_posts() -> None:
    # Given
    assert hasattr(window_contract, "build_locator_post_requests")
    locator = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    context = WindowContext(
        run_started_at=datetime(2026, 8, 21, tzinfo=UTC),
        override=ExplicitWindow.parse(None, None),
    )

    # When
    requests = window_contract.build_locator_post_requests((locator,), context)

    # Then
    assert requests == (
        instance_contract.AdapterPostLocatorRequest(
            locator, date(2026, 8, 14), date(2026, 8, 21)
        ),
    )


def test_locator_window_preserves_explicit_range() -> None:
    # Given
    assert hasattr(window_contract, "build_locator_post_requests")
    locator = parse_linkedin_locator(
        "https://www.linkedin.com/company/example-company/"
    )
    context = WindowContext(
        run_started_at=datetime(2026, 8, 21, tzinfo=UTC),
        override=ExplicitWindow.parse(date(2026, 8, 1), date(2026, 8, 3)),
    )

    # When
    requests = window_contract.build_locator_post_requests((locator,), context)

    # Then
    assert requests == (
        instance_contract.AdapterPostLocatorRequest(
            locator, date(2026, 8, 1), date(2026, 8, 3)
        ),
    )


def test_locator_attempt_contract_has_complete_resolved_and_unresolved_outcomes() -> (
    None
):
    # Given
    assert hasattr(instance_contract, "ResolvedLocatorPosts")
    assert hasattr(instance_contract, "UnresolvedLocatorPosts")
    assert hasattr(instance_contract, "LocatorPostsBatchCompleted")
    locator = parse_linkedin_locator("https://www.linkedin.com/in/example-person/")
    account = make_account(AccountKind.PERSON, 1)
    collected = CollectedAccount(account.id, (make_post(account.id, 1),))

    # When
    completed = instance_contract.LocatorPostsBatchCompleted(
        (
            instance_contract.ResolvedLocatorPosts(locator, account, collected),
            instance_contract.UnresolvedLocatorPosts(locator),
        )
    )

    # Then
    resolved, unresolved = completed.outcomes
    assert isinstance(resolved, instance_contract.ResolvedLocatorPosts)
    assert isinstance(unresolved, instance_contract.UnresolvedLocatorPosts)
    assert resolved.account is account
    assert resolved.collected is collected
    assert unresolved.locator is locator


def test_registry_resolution_preserves_declared_candidate_order() -> None:
    # Given
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=False,
    )
    class SecondFakeDriver(DeclaredFakeDriver):
        pass

    registry = AdapterRegistry((SecondFakeDriver, FakeDriver))

    # When
    result = registry.resolve(
        platform=Platform.LINKEDIN,
        operation=AdapterOperation.COLLECT_ACCOUNT_POSTS,
        account_kind=AccountKind.PERSON,
    )

    # Then
    assert isinstance(result, ResolvedAdapterDrivers)
    assert result.driver_classes == (SecondFakeDriver, FakeDriver)
