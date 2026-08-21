from __future__ import annotations

from tests.integration import test_brightdata_client_errors as errors
from tests.integration import test_brightdata_client_requests as requests

test_company_posts_follow_owned_snapshot_to_ready_download = (
    requests.test_company_posts_follow_owned_snapshot_to_ready_download
)
test_snapshot_id_cannot_escape_fixed_provider_endpoints = (
    requests.test_snapshot_id_cannot_escape_fixed_provider_endpoints
)
test_person_posts_accept_jsonl_and_exact_trigger_contract = (
    requests.test_person_posts_accept_jsonl_and_exact_trigger_contract
)
test_retryable_statuses_use_exact_bounded_backoff = (
    requests.test_retryable_statuses_use_exact_bounded_backoff
)
test_http_failures_map_without_provider_text = (
    errors.test_http_failures_map_without_provider_text
)
test_schema_failures_are_typed_and_redacted = (
    errors.test_schema_failures_are_typed_and_redacted
)
test_terminal_snapshot_status_never_downloads = (
    errors.test_terminal_snapshot_status_never_downloads
)
test_malformed_json_is_a_sanitized_schema_failure = (
    errors.test_malformed_json_is_a_sanitized_schema_failure
)
test_include_error_record_is_typed_input_failure = (
    errors.test_include_error_record_is_typed_input_failure
)
test_snapshot_poll_failure_retains_accepted_ownership = (
    errors.test_snapshot_poll_failure_retains_accepted_ownership
)
test_logs_never_contain_credentials_urls_or_provider_text = (
    errors.test_logs_never_contain_credentials_urls_or_provider_text
)
test_snapshot_poll_timeout_is_distinct_and_does_not_retrigger = (
    errors.test_snapshot_poll_timeout_is_distinct_and_does_not_retrigger
)
test_batch_bounds_reject_zero_and_twenty_one_without_io = (
    errors.test_batch_bounds_reject_zero_and_twenty_one_without_io
)
