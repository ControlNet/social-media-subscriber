from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.providers.brightdata.constants import (
    COMPANY_IDENTITY_DATASET,
    LINKEDIN_POSTS_DATASET,
    PERSON_IDENTITY_DATASET,
)
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
from social_media_subscriber.serialization.json import read_json
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.snapshot import SnapshotManifest
from tests.e2e.brightdata_server import (
    ACTIVE_VALUE,
    CHANGED_PERSON_URL,
    MEDIA_CANARY,
    OWNERSHIP_CANARY,
    PERSON_URL,
    REVOKED_VALUE,
    FakeBrightDataServer,
    PersonPostScenario,
)
from tests.e2e.git_harness import git, publication_root, publish
from tests.e2e.pipeline_harness import (
    invoke_collect,
    publication_app,
    report,
    tree,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_posts_first_unknown_profile_failover_preserves_source_and_redacts_canonical(
    tmp_path: Path,
) -> None:
    # Given
    previous = tmp_path / "absent"
    candidate = tmp_path / "candidate"
    server = FakeBrightDataServer()

    # When
    with server:
        result = invoke_collect(server, previous, candidate)

    # Then
    assert not server.thread_alive
    assert result.exit_code == 0
    manifest = read_json(candidate / "snapshot.json", SnapshotManifest)
    assert report(result) == {
        "candidate_change": "changed",
        "command": "collect",
        "digest": manifest.digest,
        "exit_code": 0,
        "failed_account_ids": [],
        "failed_accounts": 0,
        "succeeded_accounts": 1,
    }
    assert (
        manifest.account_count,
        manifest.post_count,
        manifest.source_record_count,
    ) == (1, 3, 4)
    requests = server.scenario.requests
    assert [item.endpoint for item in requests] == [
        "trigger",
        "trigger",
        "trigger",
        "trigger",
        "progress",
        "download",
    ]
    assert [item.credential for item in requests] == [
        "revoked",
        "revoked",
        "revoked",
        "active",
        "active",
        "active",
    ]
    assert {item.credential for item in requests} == {"revoked", "active"}
    assert {item.dataset for item in requests} == {LINKEDIN_POSTS_DATASET}
    assert {
        PERSON_IDENTITY_DATASET,
        COMPANY_IDENTITY_DATASET,
    }.isdisjoint(item.dataset for item in requests)
    assert server.scenario.trigger_calls == 4
    assert server.scenario.progress_calls == 1
    assert server.scenario.download_calls == 1
    assert server.scenario.scrape_calls == 0
    assert server.scenario.identity_calls == 0
    post_requests = [item for item in requests if item.discovery is not None]
    assert {
        (entry["start_date"], entry["end_date"])
        for request in post_requests
        for entry in request.body
    } == {("2026-08-17T00:00:00.000Z", "2026-08-20T23:59:59.999Z")}
    snapshot_tree = tree(candidate)
    source = {
        path: payload
        for path, payload in snapshot_tree.items()
        if path.startswith("source/")
    }
    canonical = {
        path: payload
        for path, payload in snapshot_tree.items()
        if not path.startswith("source/")
    }
    assert len(source) == 4
    assert len(list((candidate / "accounts").glob("*.json"))) == 1
    assert len(list((candidate / "posts/linkedin").glob("*.json"))) == 3
    source_records = [
        read_json(path, BrightDataLinkedInPostSourceRecord)
        for path in (candidate / "source").rglob("*.json")
    ]
    assert all(
        record.payload.get("profile_url") == PERSON_URL for record in source_records
    )
    assert any(
        record.payload.get("unknown_nested") == {"future": [True, None, {"n": 3}]}
        for record in source_records
    )
    assert any(
        record.payload.get("future_field") == {"preserved": True}
        for record in source_records
    )
    assert any(MEDIA_CANARY.encode() in item for item in source.values())
    assert all(MEDIA_CANARY.encode() not in item for item in canonical.values())
    assert (
        len([line for line in result.output.splitlines() if line.startswith("{")]) == 1
    )
    assert REVOKED_VALUE not in result.output
    assert ACTIVE_VALUE not in result.output


def test_posts_first_changed_slug_reconciles_same_numeric_account(
    tmp_path: Path,
) -> None:
    # Given
    first = tmp_path / "first"
    renamed = tmp_path / "renamed"
    with FakeBrightDataServer() as initial_server:
        assert invoke_collect(initial_server, tmp_path / "absent", first).exit_code == 0
    server = FakeBrightDataServer()
    server.scenario.person_actor_url = CHANGED_PERSON_URL

    # When
    with server:
        result = invoke_collect(
            server,
            first,
            renamed,
            accounts=CHANGED_PERSON_URL,
            credentials=ACTIVE_VALUE,
        )

    # Then
    state = SnapshotRepository(renamed).load_optional()
    assert result.exit_code == 0
    assert state is not None
    assert len(state.accounts) == 1
    assert state.accounts[0].platform_account_id == "101"
    assert {PERSON_URL, CHANGED_PERSON_URL}.issubset(
        {state.accounts[0].profile_url, *state.accounts[0].url_aliases}
    )
    assert server.scenario.identity_calls == server.scenario.scrape_calls == 0
    assert [request.endpoint for request in server.scenario.requests] == [
        "trigger",
        "progress",
        "download",
    ]


@pytest.mark.parametrize(
    "person_result",
    [PersonPostScenario.ZERO, PersonPostScenario.NONORIGINAL_ONLY],
    ids=["zero_records", "nonoriginal_only"],
)
def test_posts_first_zero_or_nonoriginal_writes_valid_empty_candidate(
    tmp_path: Path,
    person_result: PersonPostScenario,
) -> None:
    # Given
    candidate = tmp_path / "candidate"
    server = FakeBrightDataServer()
    server.scenario.person_result = person_result

    # When
    with server:
        result = invoke_collect(
            server,
            tmp_path / "absent",
            candidate,
            credentials=ACTIVE_VALUE,
        )

    # Then
    manifest = read_json(candidate / "snapshot.json", SnapshotManifest)
    assert result.exit_code == 4
    assert report(result)["failed_accounts"] == 1
    assert report(result)["succeeded_accounts"] == 0
    assert (
        manifest.account_count,
        manifest.post_count,
        manifest.source_record_count,
    ) == (0, 0, 0)
    assert not list((candidate / "accounts").glob("*.json"))
    assert not list((candidate / "posts/linkedin").glob("*.json"))
    assert not list((candidate / "source").rglob("*.json"))
    assert server.scenario.identity_calls == server.scenario.scrape_calls == 0


@pytest.mark.parametrize(
    "person_result",
    [PersonPostScenario.ZERO, PersonPostScenario.NONORIGINAL_ONLY],
    ids=["zero_records", "nonoriginal_only"],
)
def test_posts_first_zero_or_nonoriginal_preserves_prior_tree(
    tmp_path: Path,
    person_result: PersonPostScenario,
) -> None:
    # Given
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    with FakeBrightDataServer() as initial_server:
        assert (
            invoke_collect(initial_server, tmp_path / "absent", baseline).exit_code == 0
        )
    before = tree(baseline)
    server = FakeBrightDataServer()
    server.scenario.person_result = person_result
    server.scenario.person_actor_url = CHANGED_PERSON_URL

    # When
    with server:
        result = invoke_collect(
            server,
            baseline,
            candidate,
            accounts=CHANGED_PERSON_URL,
            credentials=ACTIVE_VALUE,
        )

    # Then
    assert result.exit_code == 4
    assert report(result)["failed_accounts"] == 1
    assert report(result)["succeeded_accounts"] == 0
    assert SnapshotRepository(candidate).load_optional() is not None
    assert tree(candidate) == before
    assert server.scenario.identity_calls == server.scenario.scrape_calls == 0
    assert server.scenario.trigger_calls == 1
    assert server.scenario.progress_calls == 1
    assert server.scenario.download_calls == 1


def test_publish_is_idempotent_metric_only_and_stale_safe(tmp_path: Path) -> None:
    # Given
    first = tmp_path / "first"
    repeated = tmp_path / "repeated"
    metric_changed = tmp_path / "metric-changed"
    source, remote = publication_root(tmp_path)
    server = FakeBrightDataServer()
    with server:
        initial_collect = invoke_collect(server, tmp_path / "absent", first)
    assert initial_collect.exit_code == 0

    # When
    app = publication_app()
    initial_publish = publish(app, source, first, "absent")
    initial_sha = git(remote, "rev-parse", "refs/heads/dist")
    with FakeBrightDataServer() as repeated_server:
        repeated_collect = invoke_collect(repeated_server, first, repeated)
    repeated_publish = publish(app, source, repeated, initial_sha)
    changed_server = FakeBrightDataServer()
    changed_server.scenario.metric = 43
    with changed_server:
        changed_collect = invoke_collect(changed_server, repeated, metric_changed)
    changed_publish = publish(app, source, metric_changed, initial_sha)
    changed_sha = git(remote, "rev-parse", "refs/heads/dist")

    # Then
    assert initial_publish.exit_code == repeated_publish.exit_code == 0
    assert report(initial_publish)["result"] == "published"
    assert report(repeated_publish)["result"] == "unchanged"
    assert repeated_collect.exit_code == 0
    assert report(repeated_collect)["candidate_change"] == "unchanged"
    assert tree(repeated) == tree(first)
    assert git(remote, "rev-parse", "refs/heads/dist") == changed_sha
    assert changed_collect.exit_code == changed_publish.exit_code == 0
    assert report(changed_publish)["result"] == "published"
    first_tree = tree(first)
    changed_tree = tree(metric_changed)
    changed_paths = {
        path
        for path in first_tree | changed_tree
        if first_tree.get(path) != changed_tree.get(path)
    }
    assert "snapshot.json" in changed_paths
    changed_source_paths = {
        path for path in changed_paths if path.startswith("source/")
    }
    assert len(changed_paths) == 2
    assert len(changed_source_paths) == 1
    assert git(remote, "rev-list", "--max-parents=0", "refs/heads/dist") == changed_sha
    assert git(remote, "rev-list", "--parents", "-n", "1", changed_sha) == changed_sha

    competing = tmp_path / "competing"
    _ = git(tmp_path, "clone", "--quiet", str(remote), str(competing))
    _ = git(competing, "checkout", "--quiet", "dist")
    _ = git(competing, "config", "user.name", "Task 14 Test")
    _ = git(competing, "config", "user.email", "task14@example.invalid")
    _ = git(competing, "commit", "--allow-empty", "--quiet", "-m", "competing")
    _ = git(competing, "push", "--quiet", "origin", "dist")
    source_head = (source / ".git/HEAD").read_bytes()
    stale = publish(app, source, metric_changed, changed_sha)
    assert stale.exit_code == 6
    assert report(stale) == {
        "command": "publish-dist",
        "error_category": "publication",
        "exit_code": 6,
    }
    assert (source / ".git/HEAD").read_bytes() == source_head


def test_partial_collection_preserves_history_and_publishes_alert_state(
    tmp_path: Path,
) -> None:
    # Given
    baseline = tmp_path / "baseline"
    partial = tmp_path / "partial"
    with FakeBrightDataServer() as server:
        assert invoke_collect(server, tmp_path / "absent", baseline).exit_code == 0
    source, remote = publication_root(tmp_path)
    app = publication_app()
    assert publish(app, source, baseline, "absent").exit_code == 0
    baseline_sha = git(remote, "rev-parse", "refs/heads/dist")
    failing = FakeBrightDataServer()
    failing.scenario.fail_person_posts = True

    # When
    with failing:
        collected = invoke_collect(failing, baseline, partial)
    published = publish(app, source, partial, baseline_sha)

    # Then
    assert collected.exit_code == 4
    assert report(collected)["failed_accounts"] == 1
    assert report(collected)["succeeded_accounts"] == 0
    assert report(collected)["candidate_change"] == "unchanged"
    assert tree(partial) == tree(baseline)
    assert published.exit_code == 0
    assert report(published)["result"] == "unchanged"
    assert git(remote, "rev-parse", "refs/heads/dist") == baseline_sha


def test_accepted_snapshot_and_ownership_failure_do_not_reroute_or_leak(
    tmp_path: Path,
) -> None:
    # Given
    accepted = FakeBrightDataServer()
    accepted.scenario.accepted_snapshot_failure = True
    ownership = FakeBrightDataServer()
    ownership.scenario.person_result = PersonPostScenario.OWNERSHIP_CONFLICT

    # When
    with accepted:
        owned = invoke_collect(
            accepted,
            tmp_path / "none",
            tmp_path / "accepted",
            accounts=PERSON_URL,
            credentials=ACTIVE_VALUE,
        )
    with ownership:
        conflict = invoke_collect(
            ownership,
            tmp_path / "none",
            tmp_path / "ownership",
            accounts=PERSON_URL,
            credentials=ACTIVE_VALUE,
        )
    with FakeBrightDataServer() as unused:
        malformed = invoke_collect(
            unused,
            tmp_path / "none",
            tmp_path / "malformed",
            accounts="https://www.linkedin.com/in/../",
        )

    # Then
    assert owned.exit_code == 4
    assert conflict.exit_code == 5
    assert malformed.exit_code == 2
    assert (tmp_path / "accepted/snapshot.json").is_file()
    assert not (tmp_path / "ownership").exists()
    assert accepted.scenario.trigger_calls == 1
    assert accepted.scenario.progress_calls == 1
    assert accepted.scenario.download_calls == 0
    assert [item.endpoint for item in accepted.scenario.requests] == [
        "trigger",
        "progress",
    ]
    assert ownership.scenario.trigger_calls == 1
    assert ownership.scenario.progress_calls == 1
    assert ownership.scenario.download_calls == 1
    assert {item.credential for item in ownership.scenario.requests} == {"active"}
    assert accepted.scenario.identity_calls == accepted.scenario.scrape_calls == 0
    assert ownership.scenario.identity_calls == ownership.scenario.scrape_calls == 0
    assert not accepted.thread_alive
    assert not ownership.thread_alive
    assert unused.scenario.requests == []
    combined = owned.output + conflict.output + malformed.output
    assert REVOKED_VALUE not in combined
    assert ACTIVE_VALUE not in combined
    assert MEDIA_CANARY not in combined
    assert OWNERSHIP_CANARY not in combined
    assert PERSON_URL not in combined
