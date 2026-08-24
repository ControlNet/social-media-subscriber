from __future__ import annotations

from typing import TYPE_CHECKING

from tests.e2e.brightdata_server import FakeBrightDataServer
from tests.e2e.git_harness import git, publication_root, publish
from tests.e2e.pipeline_harness import invoke_collect, publication_app, report, tree

if TYPE_CHECKING:
    from pathlib import Path


def assert_metric_publication_is_idempotent_and_stale_safe(tmp_path: Path) -> None:
    first = tmp_path / "first"
    repeated = tmp_path / "repeated"
    metric_changed = tmp_path / "metric-changed"
    source, remote = publication_root(tmp_path)
    server = FakeBrightDataServer()
    with server:
        initial_collect = invoke_collect(server, tmp_path / "absent", first)
    assert initial_collect.exit_code == 0

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
    assert len(changed_paths) == 1
    assert next(iter(changed_paths)).startswith("posts/linkedin/")
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


def assert_partial_collection_preserves_history_and_publishes_alert(
    tmp_path: Path,
) -> None:
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

    with failing:
        collected = invoke_collect(failing, baseline, partial)
    published = publish(app, source, partial, baseline_sha)

    assert collected.exit_code == 4
    assert report(collected)["failed_accounts"] == 1
    assert report(collected)["succeeded_accounts"] == 0
    assert report(collected)["candidate_change"] == "unchanged"
    assert tree(partial) == tree(baseline)
    assert published.exit_code == 0
    assert report(published)["result"] == "unchanged"
    assert git(remote, "rev-parse", "refs/heads/dist") == baseline_sha
