from __future__ import annotations

from tests.unit import (
    test_router_accepted_snapshot as accepted_snapshot,
)
from tests.unit import (
    test_router_account_failover as account_failover,
)
from tests.unit import (
    test_router_account_ownership as account_ownership,
)
from tests.unit import (
    test_router_account_routing as account_routing,
)
from tests.unit import (
    test_router_contracts as contracts,
)
from tests.unit import (
    test_router_identity as identity,
)
from tests.unit import (
    test_router_locator_failover as locator_failover,
)
from tests.unit import (
    test_router_locator_ownership as locator_ownership,
)
from tests.unit import (
    test_router_locator_routing as locator_routing,
)
from tests.unit import (
    test_router_locator_schema as locator_schema,
)

test_locator_discovery_empty_requests_succeed_without_provider_calls = (
    locator_routing.test_locator_discovery_empty_requests_succeed_without_provider_calls
)
test_locator_operation_contract_is_closed = (
    contracts.test_locator_operation_contract_is_closed
)
test_locator_batch_deduplicates_canonical_requests_and_rejects_mixed_kinds = (
    contracts.test_locator_batch_deduplicates_canonical_requests_and_rejects_mixed_kinds
)
test_locator_batch_rejects_conflicting_duplicate_windows_before_fake_calls = (
    contracts.test_locator_batch_rejects_conflicting_duplicate_windows_before_fake_calls
)
test_locator_request_rejects_inverted_window_before_fake_calls = (
    contracts.test_locator_request_rejects_inverted_window_before_fake_calls
)
test_locator_window_uses_initial_policy_without_reading_prior_posts = (
    contracts.test_locator_window_uses_initial_policy_without_reading_prior_posts
)
test_locator_window_preserves_explicit_range = (
    contracts.test_locator_window_preserves_explicit_range
)
test_locator_attempt_contract_has_complete_resolved_and_unresolved_outcomes = (
    contracts.locator_attempt_contract
)
test_post_route_rejects_discovery_before_provider_call = (
    identity.test_post_route_rejects_discovery_before_provider_call
)
test_registry_resolution_preserves_declared_candidate_order = (
    contracts.test_registry_resolution_preserves_declared_candidate_order
)
test_empty_account_set_succeeds_without_provider_calls = (
    account_routing.test_empty_account_set_succeeds_without_provider_calls
)
test_post_route_rejects_identity_operation_before_provider_call = (
    identity.test_post_route_rejects_identity_operation_before_provider_call
)
test_unique_instance_is_created_per_first_seen_credential = (
    identity.test_unique_instance_is_created_per_first_seen_credential
)
test_zero_instance_pool_returns_account_scoped_exhaustion = (
    account_routing.test_zero_instance_pool_returns_account_scoped_exhaustion
)
test_batches_are_bounded_and_stably_distributed = (
    account_routing.test_batches_are_bounded_and_stably_distributed
)
test_person_and_company_batches_are_separate_and_stable = (
    account_routing.test_person_and_company_batches_are_separate_and_stable
)
test_disabled_instance_fails_over_once_for_the_run = (
    account_failover.test_disabled_instance_fails_over_once_for_the_run
)
test_transient_pre_acceptance_failure_tries_each_instance_once = (
    account_failover.test_transient_pre_acceptance_failure_tries_each_instance_once
)
test_accepted_snapshot_failure_never_retriggers_another_instance = (
    accepted_snapshot.test_accepted_snapshot_failure_never_retriggers_another_instance
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
test_duplicate_post_ids_are_idempotent_within_a_result = (
    account_ownership.test_duplicate_post_ids_are_idempotent_within_a_result
)
test_health_is_fresh_for_each_route_call = (
    account_failover.test_health_is_fresh_for_each_route_call
)
test_equivalent_source_records_collapse_with_deterministic_skips = (
    account_ownership.test_equivalent_source_records_collapse_with_deterministic_skips
)
test_differing_source_payload_aborts_and_suppresses_all_output = (
    account_ownership.test_differing_source_payload_aborts_and_suppresses_all_output
)
test_source_account_ownership_mismatch_aborts_without_output = (
    account_ownership.test_source_account_ownership_mismatch_aborts_without_output
)
test_locator_discovery_batch_21_is_20_plus_1_in_stable_order = (
    locator_routing.test_locator_discovery_batch_21_is_20_plus_1_in_stable_order
)
test_locator_discovery_kind_partitions_people_before_companies = (
    locator_routing.test_locator_discovery_kind_partitions_people_before_companies
)
test_locator_discovery_canonical_dedupe_and_conflicting_window = (
    locator_routing.test_locator_discovery_canonical_dedupe_and_conflicting_window
)
test_locator_discovery_failover_disables_instance = (
    locator_failover.test_locator_discovery_failover_disables_instance
)
test_locator_discovery_retryable_failure_rotates_without_disabling = (
    locator_failover.test_locator_discovery_retryable_failure_rotates_without_disabling
)
test_locator_discovery_health_is_fresh_for_each_call = (
    locator_failover.test_locator_discovery_health_is_fresh_for_each_call
)
test_locator_discovery_fail_attribution_for_unsupported_or_empty_pool = (
    locator_failover.failure_attribution
)
test_locator_discovery_accepted_snapshot_is_terminal_no_reroute = (
    accepted_snapshot.test_locator_discovery_accepted_snapshot_is_terminal_no_reroute
)
test_locator_discovery_schema_requires_exact_complete_coverage = (
    locator_schema.test_locator_discovery_schema_requires_exact_complete_coverage
)
test_locator_discovery_cross_locator_account_owner_aborts = (
    locator_ownership.test_locator_discovery_cross_locator_account_owner_aborts
)
test_locator_discovery_cross_locator_failure_is_order_independent = (
    locator_ownership.test_locator_discovery_cross_locator_failure_is_order_independent
)
test_locator_discovery_ownership_mismatch_aborts = (
    locator_ownership.test_locator_discovery_ownership_mismatch_aborts
)
test_locator_discovery_schema_conflicting_duplicate_sources_abort = (
    locator_schema.test_locator_discovery_schema_conflicting_duplicate_sources_abort
)
test_locator_discovery_schema_conflicting_duplicate_posts_abort = (
    locator_schema.test_locator_discovery_schema_conflicting_duplicate_posts_abort
)
test_locator_discovery_late_schema_abort_suppresses_prior_batch = (
    locator_schema.test_locator_discovery_late_schema_abort_suppresses_prior_batch
)
test_locator_discovery_resolved_unresolved_and_posts_are_complete = (
    locator_ownership.test_locator_discovery_resolved_unresolved_and_posts_are_complete
)
