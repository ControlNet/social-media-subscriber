from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.e2e.brightdata_server import PersonPostScenario
from tests.e2e.failure_scenarios import (
    assert_accepted_snapshot_and_ownership_failures_do_not_leak,
    assert_invalid_schema_aborts_without_candidate_or_leak,
)
from tests.e2e.posts_first_scenarios import (
    assert_changed_slug_reconciles_same_numeric_account,
    assert_empty_candidate,
    assert_empty_result_preserves_prior_tree,
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


def test_posts_first_changed_slug_reconciles_same_numeric_account(
    tmp_path: Path,
) -> None:
    assert_changed_slug_reconciles_same_numeric_account(tmp_path)


@pytest.mark.parametrize(
    "person_result",
    [PersonPostScenario.ZERO, PersonPostScenario.NONORIGINAL_ONLY],
    ids=["zero_records", "nonoriginal_only"],
)
def test_posts_first_zero_or_nonoriginal_writes_valid_empty_candidate(
    tmp_path: Path,
    person_result: PersonPostScenario,
) -> None:
    assert_empty_candidate(tmp_path, person_result)


@pytest.mark.parametrize(
    "person_result",
    [PersonPostScenario.ZERO, PersonPostScenario.NONORIGINAL_ONLY],
    ids=["zero_records", "nonoriginal_only"],
)
def test_posts_first_zero_or_nonoriginal_preserves_prior_tree(
    tmp_path: Path,
    person_result: PersonPostScenario,
) -> None:
    assert_empty_result_preserves_prior_tree(tmp_path, person_result)


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
