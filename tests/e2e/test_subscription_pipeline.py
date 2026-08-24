from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.e2e.brightdata_server import (
    ACTIVE_VALUE,
    CHANGED_PERSON_URL,
    PERSON_URL,
    FakeBrightDataServer,
    PersonPostScenario,
)
from tests.e2e.brightdata_server_fixtures import PERSON_POST_IDS
from tests.e2e.failure_scenarios import (
    assert_accepted_snapshot_and_ownership_failures_do_not_leak,
    assert_invalid_schema_aborts_without_candidate_or_leak,
)
from tests.e2e.pipeline_harness import invoke_collect, report, tree
from tests.e2e.posts_first_scenarios import (
    assert_changed_slug_creates_distinct_url_account,
    assert_empty_candidate,
    assert_empty_result_adds_distinct_url_account,
    assert_snapshot_metadata,
    assert_unknown_profile_failover,
)
from tests.e2e.publication_scenarios import (
    assert_metric_publication_is_idempotent_and_stale_safe,
    assert_partial_collection_preserves_history_and_publishes_alert,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_posts_first_unknown_profile_failover_preserves_source_and_redacts_canonical(
    tmp_path: Path,
) -> None:
    assert_unknown_profile_failover(tmp_path)


def test_exact_rediscovery_preserves_first_seen_and_is_byte_identical(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline"
    refreshed = tmp_path / "refreshed"
    repeated = tmp_path / "repeated"
    initial_server = FakeBrightDataServer()
    initial_server.scenario.person_result = PersonPostScenario.ZERO

    with initial_server:
        initial = invoke_collect(
            initial_server,
            tmp_path / "absent",
            baseline,
            credentials=ACTIVE_VALUE,
        )
    baseline_state, _ = assert_snapshot_metadata(
        baseline, account_urls=(PERSON_URL,), post_ids=()
    )
    with FakeBrightDataServer() as refresh_server:
        refresh = invoke_collect(
            refresh_server,
            baseline,
            refreshed,
            credentials=ACTIVE_VALUE,
        )
    refreshed_state, refreshed_manifest = assert_snapshot_metadata(
        refreshed,
        account_urls=(PERSON_URL,),
        post_ids=PERSON_POST_IDS,
    )
    with FakeBrightDataServer() as repeated_server:
        rediscovered = invoke_collect(
            repeated_server,
            refreshed,
            repeated,
            credentials=ACTIVE_VALUE,
        )
    repeated_state, repeated_manifest = assert_snapshot_metadata(
        repeated,
        account_urls=(PERSON_URL,),
        post_ids=PERSON_POST_IDS,
    )

    baseline_first_seen = baseline_state.accounts[0].first_seen_at
    assert initial.exit_code == refresh.exit_code == rediscovered.exit_code == 0
    assert len(refreshed_state.accounts) == len(repeated_state.accounts) == 1
    assert len(refreshed_state.posts) == len(repeated_state.posts) == 4
    assert refreshed_state.accounts[0].first_seen_at == baseline_first_seen
    assert repeated_state.accounts[0].first_seen_at == baseline_first_seen
    assert report(rediscovered) == {
        "candidate_change": "unchanged",
        "command": "collect",
        "digest": refreshed_manifest.digest,
        "exit_code": 0,
        "failed_account_ids": [],
        "failed_accounts": 0,
        "succeeded_accounts": 1,
    }
    assert repeated_manifest.digest == refreshed_manifest.digest
    assert tree(repeated) == tree(refreshed)
    for server in (initial_server, refresh_server, repeated_server):
        assert server.scenario.scrape_calls == 0
        assert server.scenario.trigger_calls == 1
        assert not server.thread_alive


def test_posts_first_changed_slug_creates_distinct_url_account(
    tmp_path: Path,
) -> None:
    _ = assert_changed_slug_creates_distinct_url_account(tmp_path)


def test_changed_slug_creates_distinct_url_accounts(tmp_path: Path) -> None:
    discovered_urls = assert_changed_slug_creates_distinct_url_account(tmp_path)
    account_urls = tuple(reversed(discovered_urls))
    old_url, changed_url = PERSON_URL, CHANGED_PERSON_URL
    assert account_urls == (old_url, changed_url)  # RED-PROBE-T10


def test_failed_refresh_preserves_history_and_previous_bytes(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "failed-refresh"
    with FakeBrightDataServer() as initial_server:
        initial = invoke_collect(
            initial_server,
            tmp_path / "absent",
            baseline,
            credentials=ACTIVE_VALUE,
        )
    baseline_state, baseline_manifest = assert_snapshot_metadata(
        baseline,
        account_urls=(PERSON_URL,),
        post_ids=PERSON_POST_IDS,
    )
    before = tree(baseline)
    failed_server = FakeBrightDataServer()
    failed_server.scenario.accepted_snapshot_failure = True

    with failed_server:
        failed = invoke_collect(
            failed_server,
            baseline,
            candidate,
            credentials=ACTIVE_VALUE,
        )
    candidate_state, candidate_manifest = assert_snapshot_metadata(
        candidate,
        account_urls=(PERSON_URL,),
        post_ids=PERSON_POST_IDS,
    )

    assert initial.exit_code == 0
    assert failed.exit_code == 4
    assert report(failed) == {
        "candidate_change": "unchanged",
        "command": "collect",
        "digest": baseline_manifest.digest,
        "exit_code": 4,
        "failed_account_ids": [PERSON_URL],
        "failed_accounts": 1,
        "succeeded_accounts": 0,
    }
    assert candidate_state == baseline_state
    assert candidate_manifest.digest == baseline_manifest.digest
    assert tree(candidate) == before
    assert tree(baseline) == before
    assert [request.endpoint for request in failed_server.scenario.requests] == [
        "trigger",
        "progress",
    ]
    assert failed_server.scenario.download_calls == 0
    for server in (initial_server, failed_server):
        assert server.scenario.scrape_calls == 0
        assert server.scenario.trigger_calls == 1
        assert not server.thread_alive


@pytest.mark.parametrize(
    "person_result",
    [PersonPostScenario.ZERO, PersonPostScenario.NONORIGINAL_ONLY],
    ids=["zero_records", "nonoriginal_only"],
)
def test_posts_first_zero_or_nonoriginal_writes_valid_candidate(
    tmp_path: Path,
    person_result: PersonPostScenario,
) -> None:
    assert_empty_candidate(tmp_path, person_result)


def test_posts_first_zero_posts_adds_distinct_url_account(tmp_path: Path) -> None:
    assert_empty_result_adds_distinct_url_account(tmp_path, PersonPostScenario.ZERO)


def test_publish_is_idempotent_metric_only_and_stale_safe(tmp_path: Path) -> None:
    assert_metric_publication_is_idempotent_and_stale_safe(tmp_path)


def test_partial_collection_preserves_history_and_publishes_alert_state(
    tmp_path: Path,
) -> None:
    assert_partial_collection_preserves_history_and_publishes_alert(tmp_path)


def test_accepted_snapshot_and_ownership_failure_do_not_reroute_or_leak(
    tmp_path: Path,
) -> None:
    assert_accepted_snapshot_and_ownership_failures_do_not_leak(tmp_path)


def test_posts_first_invalid_schema_aborts_without_candidate_or_leak(
    tmp_path: Path,
) -> None:
    assert_invalid_schema_aborts_without_candidate_or_leak(tmp_path)
