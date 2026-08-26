from __future__ import annotations

import pytest

from social_media_subscriber.adapters.instance import AcceptedSnapshotBatchFailure
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
)
from social_media_subscriber.domain.platform import AccountKind
from tests.fakes.router import CompleteBatch, make_account
from tests.unit import test_router_account_failover as account_failover
from tests.unit import test_router_account_ownership as account_ownership
from tests.unit import test_router_account_routing as account_routing
from tests.unit import test_router_contracts as contracts
from tests.unit.test_router_support import build_post_requests, build_router

test_adapter_surface_exposes_only_normal_account_posts = (
    contracts.test_adapter_surface_exposes_only_normal_account_posts
)
test_registry_resolution_preserves_declared_candidate_order = (
    contracts.test_registry_resolution_preserves_declared_candidate_order
)
test_empty_account_set_succeeds_without_provider_calls = (
    account_routing.test_empty_account_set_succeeds_without_provider_calls
)
test_zero_instance_pool_returns_account_scoped_exhaustion = (
    account_routing.test_zero_instance_pool_returns_account_scoped_exhaustion
)
test_batches_are_bounded_and_start_with_first_source = (
    account_routing.test_batches_are_bounded_and_start_with_first_source
)
test_person_and_company_batches_are_separate_and_stable = (
    account_routing.test_person_and_company_batches_are_separate_and_stable
)
test_mixed_platform_batches_use_platform_specific_drivers_in_order = (
    account_routing.test_mixed_platform_batches_use_platform_specific_drivers_in_order
)
test_unregistered_x_capability_is_an_account_scoped_failure = (
    account_routing.test_unregistered_x_capability_is_an_account_scoped_failure
)
test_non_batching_compatible_driver_forces_single_account_batches = (
    account_routing.test_non_batching_compatible_driver_forces_single_account_batches
)
test_router_creates_one_instance_per_source_with_the_same_driver = (
    account_routing.test_router_creates_one_instance_per_source_with_the_same_driver
)
test_router_rejects_unregistered_instance_spec = (
    account_routing.test_router_rejects_unregistered_instance_spec
)
test_router_rejects_factory_instance_for_a_different_driver = (
    account_routing.test_router_rejects_factory_instance_for_a_different_driver
)
test_disabled_instance_fails_over_once_for_the_run = (
    account_failover.test_disabled_instance_fails_over_once_for_the_run
)
test_transient_pre_acceptance_failure_tries_each_instance_once = (
    account_failover.test_transient_pre_acceptance_failure_tries_each_instance_once
)
test_retryable_failure_can_fall_back_to_another_provider_driver = (
    account_failover.test_retryable_failure_can_fall_back_to_another_provider_driver
)


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


test_invalid_account_result_never_rotates_credentials = (
    account_failover.test_invalid_account_result_never_rotates_credentials
)
test_not_found_account_is_partial_without_credential_rotation = (
    account_failover.test_not_found_account_is_partial_without_credential_rotation
)
test_schema_failure_aborts_without_failover_or_posts = (
    account_failover.test_schema_failure_aborts_without_failover_or_posts
)
test_inconsistent_batch_identity_aborts_as_schema_corruption = (
    account_failover.test_inconsistent_batch_identity_aborts_as_schema_corruption
)
test_health_is_fresh_for_each_route_call = (
    account_failover.test_health_is_fresh_for_each_route_call
)
test_duplicate_post_ids_are_idempotent_within_a_result = (
    account_ownership.test_duplicate_post_ids_are_idempotent_within_a_result
)
test_differing_canonical_post_payload_aborts_all_output = (
    account_ownership.test_differing_canonical_post_payload_aborts_all_output
)
test_post_account_ownership_mismatch_aborts_without_output = (
    account_ownership.test_post_account_ownership_mismatch_aborts_without_output
)
