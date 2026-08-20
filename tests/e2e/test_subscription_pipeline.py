from __future__ import annotations

import json
from typing import TYPE_CHECKING

from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
from social_media_subscriber.serialization.json import read_json
from tests.e2e.brightdata_server import (
    ACTIVE_VALUE,
    MEDIA_CANARY,
    PERSON_URL,
    REVOKED_VALUE,
    FakeBrightDataServer,
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


def test_collect_failover_preserves_source_and_redacts_canonical(
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
    assert result.exit_code == 0
    assert report(result) == {
        "candidate_change": "changed",
        "command": "collect",
        "digest": json.loads((candidate / "snapshot.json").read_text())["digest"],
        "exit_code": 0,
        "failed_account_ids": [],
        "failed_accounts": 0,
        "succeeded_accounts": 2,
    }
    requests = server.scenario.requests
    assert requests[0].credential == "revoked"
    assert {item.credential for item in requests} == {"revoked", "active"}
    assert requests[-1].credential == "active"
    post_requests = [item for item in requests if item.discovery is not None]
    assert {
        (entry["start_date"], entry["end_date"])
        for request in post_requests
        for entry in request.body
    } == {("2026-08-17", "2026-08-20")}
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
    assert len(source) == 3
    assert len(list((candidate / "posts/linkedin").glob("*.json"))) == 2
    source_records = [
        read_json(path, BrightDataLinkedInPostSourceRecord)
        for path in (candidate / "source").rglob("*.json")
    ]
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
    assert REVOKED_VALUE not in result.output
    assert ACTIVE_VALUE not in result.output


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
    assert report(collected)["succeeded_accounts"] == 1
    assert report(collected)["candidate_change"] == "unchanged"
    assert tree(partial) == tree(baseline)
    assert published.exit_code == 0
    assert report(published)["result"] == "unchanged"
    assert git(remote, "rev-parse", "refs/heads/dist") == baseline_sha


def test_invalid_schema_and_accepted_snapshot_abort_without_secret_leak(
    tmp_path: Path,
) -> None:
    # Given
    invalid = FakeBrightDataServer()
    invalid.scenario.invalid_identity_schema = True
    accepted = FakeBrightDataServer()
    accepted.scenario.accepted_snapshot_failure = True

    # When
    with invalid:
        schema = invoke_collect(invalid, tmp_path / "none", tmp_path / "schema")
    with accepted:
        owned = invoke_collect(
            accepted,
            tmp_path / "none",
            tmp_path / "accepted",
            accounts=PERSON_URL,
        )
    with FakeBrightDataServer() as unused:
        malformed = invoke_collect(
            unused,
            tmp_path / "none",
            tmp_path / "malformed",
            accounts="https://www.linkedin.com/in/../",
        )

    # Then
    assert schema.exit_code == 5
    assert owned.exit_code == 4
    assert malformed.exit_code == 2
    assert not (tmp_path / "schema").exists()
    assert (tmp_path / "accepted/snapshot.json").is_file()
    accepted_scrapes = [
        item
        for item in accepted.scenario.requests
        if item.discovery == "profile_url" and item.credential == "active"
    ]
    assert len(accepted_scrapes) == 1
    assert unused.scenario.requests == []
    combined = schema.output + owned.output + malformed.output
    assert REVOKED_VALUE not in combined
    assert ACTIVE_VALUE not in combined
    assert MEDIA_CANARY not in combined
