from __future__ import annotations

from tests.integration import test_collection_application_failures as failures
from tests.integration import test_collection_application_ownership as ownership
from tests.integration import test_collection_application_preflight as preflight
from tests.integration import test_collection_application_success as success

test_all_success_new_run_writes_valid_candidate = (
    success.test_all_success_new_run_writes_valid_candidate
)
test_posts_first_unknown_uses_posts_without_identity_lookup = (
    success.test_posts_first_unknown_uses_posts_without_identity_lookup
)
test_posts_first_unknown_respects_explicit_window = (
    success.test_posts_first_unknown_respects_explicit_window
)
test_overlap_rerun_is_byte_identical_no_change = (
    success.test_overlap_rerun_is_byte_identical_no_change
)
test_post_or_source_only_change_updates_candidate = (
    success.test_post_or_source_only_change_updates_candidate
)
test_known_zero_posts_preserves_history = (
    success.test_known_zero_posts_preserves_history
)
test_mixed_existing_and_new_urls_use_incremental_and_initial_windows = (
    success.test_mixed_existing_and_new_urls_use_incremental_and_initial_windows
)
test_mixed_known_unknown_merges_and_writes_exactly_once = (
    success.test_mixed_known_unknown_merges_and_writes_exactly_once
)
test_success_without_original_posts_persists_url_account = (
    failures.test_success_without_original_posts_persists_url_account
)
test_typed_failure_does_not_create_new_url_account = (
    failures.test_typed_failure_does_not_create_new_url_account
)
test_typed_failure_preserves_existing_url_history = (
    failures.test_typed_failure_preserves_existing_url_history
)
test_isolated_failure_preserves_history_and_merges_success = (
    failures.test_isolated_failure_preserves_history_and_merges_success
)
test_total_pool_failure_writes_no_candidate_and_attributes_url = (
    failures.test_total_pool_failure_writes_no_candidate_and_attributes_url
)
test_schema_abort_writes_no_candidate = failures.test_schema_abort_writes_no_candidate
test_changed_slug_creates_distinct_url_accounts = (
    ownership.test_changed_slug_creates_distinct_url_accounts
)
test_actor_ownership_conflict_preserves_prior_snapshot_bytes = (
    ownership.test_actor_ownership_conflict_preserves_prior_snapshot_bytes
)
test_duplicate_post_claimed_by_two_url_owners_is_atomic = (
    ownership.test_duplicate_post_claimed_by_two_url_owners_is_atomic
)
test_accepted_snapshot_failure_is_terminal_without_reroute = (
    ownership.test_accepted_snapshot_failure_is_terminal_without_reroute
)
test_corrupt_prior_and_invalid_override_are_preflight_failures = (
    preflight.test_corrupt_prior_and_invalid_override_are_preflight_failures
)
test_explicit_window_replaces_defaults = (
    preflight.test_explicit_window_replaces_defaults
)
test_write_fault_returns_integrity_and_preserves_prior = (
    preflight.test_write_fault_returns_integrity_and_preserves_prior
)
test_router_status_enum_remains_closed = (
    preflight.test_router_status_enum_remains_closed
)
