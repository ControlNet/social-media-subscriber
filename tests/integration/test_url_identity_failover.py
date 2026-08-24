from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    AdapterBatch,
    AdapterInstanceOrdinal,
    BatchCompleted,
    CollectedAccount,
)
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    InstanceHealthStatus,
    RouterDiagnostic,
    RouterDiagnosticCategory,
    RouterResult,
    RouterRunStatus,
)
from social_media_subscriber.domain.platform import AccountKind
from social_media_subscriber.providers.brightdata.adapter import (
    BrightDataLinkedInAdapter,
)
from social_media_subscriber.providers.brightdata.adapter_error_mapping import (
    map_provider_error,
)
from social_media_subscriber.providers.brightdata.client import BrightDataClient
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from tests.fakes.router import CompleteBatch, ScriptedFactory, make_account
from tests.unit.test_router_support import build_post_requests, build_router

if TYPE_CHECKING:
    from social_media_subscriber.adapters.instance import AdapterAttempt

_INSTANCE_NAMES = ("instance-a", "instance-b")


def _calls(factory: ScriptedFactory) -> tuple[str, ...]:
    return tuple(_INSTANCE_NAMES[int(call.ordinal)] for call in factory.calls)


def _health(result: RouterResult) -> tuple[InstanceHealthStatus, ...]:
    return tuple(item.status for item in result.health)


def _mapped_failure(
    category: BrightDataErrorCategory,
    *,
    snapshot_accepted: bool = False,
) -> AdapterAttempt:
    account = make_account(AccountKind.PERSON, 1)
    batch = AdapterBatch(build_post_requests((account,)))
    return map_provider_error(
        batch,
        BrightDataError(category, snapshot_accepted=snapshot_accepted),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "category",
    [BrightDataErrorCategory.RETRYABLE, BrightDataErrorCategory.TIMEOUT],
    ids=("retryable", "transport"),
)
async def test_retryable_then_transport_rotates_in_exact_order(
    category: BrightDataErrorCategory,
) -> None:
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router(((_mapped_failure(category),), (CompleteBatch(),)))

    result = await router.route(build_post_requests((account,)))
    calls = _calls(factory)

    assert calls == ("instance-a", "instance-b")  # RED-PROBE-T9
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    assert result.aggregate.succeeded_accounts == 1
    assert _health(result) == (
        InstanceHealthStatus.HEALTHY,
        InstanceHealthStatus.HEALTHY,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("category", "health", "diagnostic"),
    [
        (
            BrightDataErrorCategory.AUTH,
            InstanceHealthStatus.INVALID_CREDENTIAL,
            RouterDiagnosticCategory.CREDENTIAL_DISABLED,
        ),
        (
            BrightDataErrorCategory.QUOTA,
            InstanceHealthStatus.QUOTA_EXHAUSTED,
            RouterDiagnosticCategory.QUOTA_DISABLED,
        ),
    ],
    ids=("auth", "quota"),
)
async def test_auth_and_quota_disable_only_the_failed_run_instance(
    category: BrightDataErrorCategory,
    health: InstanceHealthStatus,
    diagnostic: RouterDiagnosticCategory,
) -> None:
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router(
        ((_mapped_failure(category), CompleteBatch()), (CompleteBatch(),))
    )

    first = await router.route(build_post_requests((account,)))
    second = await router.route(build_post_requests((account,)))

    assert _calls(factory) == ("instance-a", "instance-b", "instance-a")
    assert first.aggregate.status is RouterRunStatus.SUCCESS
    assert _health(first) == (health, InstanceHealthStatus.HEALTHY)
    assert first.diagnostics == (
        RouterDiagnostic(diagnostic, AdapterInstanceOrdinal(0)),
    )
    assert second.aggregate.status is RouterRunStatus.SUCCESS
    assert _health(second) == (
        InstanceHealthStatus.HEALTHY,
        InstanceHealthStatus.HEALTHY,
    )
    assert second.diagnostics == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("category", "route_failure"),
    [
        (
            BrightDataErrorCategory.INPUT,
            AccountRouteFailureCategory.INVALID_ACCOUNT,
        ),
        (
            BrightDataErrorCategory.NOT_FOUND,
            AccountRouteFailureCategory.ACCOUNT_NOT_FOUND,
        ),
    ],
    ids=("input", "not_found"),
)
async def test_input_and_not_found_are_terminal_without_rotation(
    category: BrightDataErrorCategory,
    route_failure: AccountRouteFailureCategory,
) -> None:
    account = make_account(AccountKind.PERSON, 1)
    router, factory = build_router(((_mapped_failure(category),), (CompleteBatch(),)))

    result = await router.route(build_post_requests((account,)))

    assert _calls(factory) == ("instance-a",)
    assert result.aggregate.status is RouterRunStatus.PARTIAL
    assert result.accounts == (AccountRouteFailed(account.id, route_failure),)
    assert _health(result) == (
        InstanceHealthStatus.HEALTHY,
        InstanceHealthStatus.HEALTHY,
    )


@pytest.mark.anyio
async def test_accepted_snapshot_failure_is_terminal_without_reroute() -> None:
    account = make_account(AccountKind.PERSON, 1)
    accepted = _mapped_failure(
        BrightDataErrorCategory.SNAPSHOT_TIMEOUT,
        snapshot_accepted=True,
    )
    assert isinstance(accepted, AcceptedSnapshotBatchFailure)
    router, factory = build_router(((accepted,), (CompleteBatch(),)))

    result = await router.route(build_post_requests((account,)))

    assert _calls(factory) == ("instance-a",)
    assert result.aggregate.status is RouterRunStatus.PARTIAL
    assert result.accounts == (
        AccountRouteFailed(
            account.id,
            AccountRouteFailureCategory.ACCEPTED_SNAPSHOT_FAILED,
        ),
    )
    assert result.aggregate.disabled_instances == 0
    assert _health(result) == (
        InstanceHealthStatus.HEALTHY,
        InstanceHealthStatus.HEALTHY,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ["schema", "ownership"])
async def test_schema_and_ownership_corruption_abort_without_rotation(
    corruption: str,
) -> None:
    account = make_account(AccountKind.PERSON, 1)
    if corruption == "schema":
        attempt = _mapped_failure(BrightDataErrorCategory.SCHEMA)
    else:
        wrong_owner = make_account(AccountKind.PERSON, 2)
        attempt = BatchCompleted((CollectedAccount(wrong_owner.id, ()),))
    router, factory = build_router(((attempt,), (CompleteBatch(),)))

    result = await router.route(build_post_requests((account,)))

    assert _calls(factory) == ("instance-a",)
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.accounts == ()
    assert result.posts == ()
    assert result.diagnostics == (
        RouterDiagnostic(RouterDiagnosticCategory.SCHEMA_ABORT),
    )
    assert _health(result) == (
        InstanceHealthStatus.HEALTHY,
        InstanceHealthStatus.HEALTHY,
    )


@pytest.mark.anyio
async def test_batches_are_kind_separated_bounded_and_start_with_first_source() -> None:
    people = tuple(make_account(AccountKind.PERSON, number) for number in range(1, 23))
    companies = tuple(
        make_account(AccountKind.COMPANY, number) for number in range(101, 103)
    )
    router, factory = build_router(
        ((CompleteBatch(), CompleteBatch()), (CompleteBatch(),))
    )

    result = await router.route(build_post_requests(companies + people))

    person_ids = tuple(sorted((account.id for account in people), key=str))
    company_ids = tuple(sorted((account.id for account in companies), key=str))
    observed = tuple(
        (
            _INSTANCE_NAMES[int(call.ordinal)],
            call.kind,
            call.account_ids,
        )
        for call in factory.calls
    )
    assert observed == (
        ("instance-a", AccountKind.PERSON, person_ids[:20]),
        ("instance-a", AccountKind.PERSON, person_ids[20:]),
        ("instance-a", AccountKind.COMPANY, company_ids),
    )
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    assert result.aggregate.succeeded_accounts == 24


def test_only_the_normal_posts_operation_remains_public() -> None:
    assert BrightDataLinkedInAdapter.adapter_metadata.operations == (
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
    )
    assert not hasattr(BrightDataLinkedInAdapter, "resolve_account_identity")
    assert not hasattr(BrightDataLinkedInAdapter, "resolve_identity")
    assert not hasattr(BrightDataLinkedInAdapter, "discover_posts")
    assert not hasattr(BrightDataClient, "resolve_person_identities")
    assert not hasattr(BrightDataClient, "resolve_company_identities")
