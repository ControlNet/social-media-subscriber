from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from social_media_subscriber.domain.ids import record_filename
from social_media_subscriber.providers.brightdata.constants import (
    LINKEDIN_POSTS_DATASET,
)
from social_media_subscriber.storage.layout import ACCOUNTS_INDEX
from social_media_subscriber.storage.repository import SnapshotRepository
from tests.e2e.brightdata_server import (
    ACTIVE_VALUE,
    CHANGED_PERSON_URL,
    MEDIA_CANARY,
    PERSON_URL,
    REVOKED_VALUE,
    FakeBrightDataServer,
    PersonPostScenario,
)
from tests.e2e.brightdata_server_fixtures import PERSON_POST_IDS
from tests.e2e.pipeline_harness import invoke_collect, report, tree

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from social_media_subscriber.storage.snapshot import SnapshotState, SnapshotSummary


def assert_snapshot_metadata(
    root: Path,
    *,
    account_urls: Sequence[str],
    post_ids: tuple[str, ...],
) -> tuple[SnapshotState, SnapshotSummary]:
    validated = SnapshotRepository(root).read_optional()
    assert validated is not None
    state = validated.state
    summary = validated.summary
    accounts_index = TypeAdapter(dict[str, str]).validate_json(
        (root / ACCOUNTS_INDEX).read_bytes()
    )
    expected_accounts = {
        str(account.id): f"accounts/{record_filename(account.id)}"
        for account in state.accounts
    }
    assert tuple(account.id for account in state.accounts) == tuple(account_urls)
    assert accounts_index == expected_accounts
    assert {post.id for post in state.posts} == set(post_ids)
    return state, summary


def assert_posts_only_requests(server: FakeBrightDataServer) -> None:
    assert server.scenario.scrape_calls == 0
    assert {request.dataset for request in server.scenario.requests} == {
        LINKEDIN_POSTS_DATASET
    }


def assert_unknown_profile_failover(tmp_path: Path) -> None:
    previous = tmp_path / "absent"
    candidate = tmp_path / "candidate"
    server = FakeBrightDataServer()

    with server:
        result = invoke_collect(server, previous, candidate)

    assert not server.thread_alive
    assert result.exit_code == 0
    validated = SnapshotRepository(candidate).read_optional()
    assert validated is not None
    state = validated.state
    summary = validated.summary
    assert report(result) == {
        "candidate_change": "changed",
        "command": "collect",
        "digest": summary.digest,
        "exit_code": 0,
        "failed_account_ids": [],
        "failed_accounts": 0,
        "succeeded_accounts": 1,
    }
    assert (
        summary.account_count,
        summary.post_count,
    ) == (1, 4)
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
    assert server.scenario.trigger_calls == 4
    assert server.scenario.progress_calls == 1
    assert server.scenario.download_calls == 1
    assert server.scenario.scrape_calls == 0
    post_requests = [item for item in requests if item.discovery is not None]
    assert {
        (entry["start_date"], entry["end_date"])
        for request in post_requests
        for entry in request.body
    } == {("2026-08-17T00:00:00.000Z", "2026-08-20T23:59:59.999Z")}
    snapshot_tree = tree(candidate)
    assert len(list((candidate / "accounts").glob("*.json"))) == 1
    assert len(list((candidate / "posts/linkedin").glob("*.json"))) == 4
    assert all(post.account_id == PERSON_URL for post in state.posts)
    assert any(
        post.content.get("unknown_nested") == {"future": [True, None, {"n": 3}]}
        for post in state.posts
    )
    assert any(
        post.content.get("future_field") == {"preserved": True} for post in state.posts
    )
    assert any(post.type == "repost" for post in state.posts)
    assert any(MEDIA_CANARY.encode() in item for item in snapshot_tree.values())
    assert not (candidate / "source").exists()
    assert not (candidate / "feed.json").exists()
    assert not (candidate / "snapshot.json").exists()
    assert (
        len([line for line in result.output.splitlines() if line.startswith("{")]) == 1
    )
    assert REVOKED_VALUE not in result.output
    assert ACTIVE_VALUE not in result.output


def assert_changed_slug_creates_distinct_url_account(
    tmp_path: Path,
) -> tuple[str, ...]:
    first = tmp_path / "first"
    renamed = tmp_path / "renamed"
    with FakeBrightDataServer() as initial_server:
        assert invoke_collect(initial_server, tmp_path / "absent", first).exit_code == 0
    server = FakeBrightDataServer()
    server.scenario.person_actor_url = CHANGED_PERSON_URL
    server.scenario.person_result = PersonPostScenario.ZERO

    with server:
        result = invoke_collect(
            server,
            first,
            renamed,
            accounts=CHANGED_PERSON_URL,
            credentials=ACTIVE_VALUE,
        )

    assert result.exit_code == 0
    state, summary = assert_snapshot_metadata(
        renamed,
        account_urls=(CHANGED_PERSON_URL, PERSON_URL),
        post_ids=PERSON_POST_IDS,
    )
    assert all(account.id == account.profile_url for account in state.accounts)
    assert_posts_only_requests(initial_server)
    assert_posts_only_requests(server)
    assert [request.endpoint for request in server.scenario.requests] == [
        "trigger",
        "progress",
        "download",
    ]
    assert report(result)["digest"] == summary.digest
    return tuple(str(account.id) for account in state.accounts)


def assert_empty_candidate(tmp_path: Path, person_result: PersonPostScenario) -> None:
    candidate = tmp_path / "candidate"
    server = FakeBrightDataServer()
    server.scenario.person_result = person_result

    with server:
        result = invoke_collect(
            server,
            tmp_path / "absent",
            candidate,
            credentials=ACTIVE_VALUE,
        )

    validated = SnapshotRepository(candidate).read_optional()
    assert validated is not None
    summary = validated.summary
    assert result.exit_code == 0
    assert report(result)["failed_accounts"] == 0
    assert report(result)["succeeded_accounts"] == 1
    assert (
        summary.account_count,
        summary.post_count,
    ) == (1, 0 if person_result is PersonPostScenario.ZERO else 1)
    state = validated.state
    assert tuple(account.id for account in state.accounts) == (PERSON_URL,)
    if person_result is PersonPostScenario.ZERO:
        assert state.posts == ()
    else:
        assert len(state.posts) == 1
        assert state.posts[0].type == "repost"
    assert not (candidate / "source").exists()
    assert_posts_only_requests(server)
    assert server.scenario.trigger_calls == 1


def assert_empty_result_adds_distinct_url_account(
    tmp_path: Path, person_result: PersonPostScenario
) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    with FakeBrightDataServer() as initial_server:
        assert (
            invoke_collect(initial_server, tmp_path / "absent", baseline).exit_code == 0
        )
    server = FakeBrightDataServer()
    server.scenario.person_result = person_result
    server.scenario.person_actor_url = CHANGED_PERSON_URL

    with server:
        result = invoke_collect(
            server,
            baseline,
            candidate,
            accounts=CHANGED_PERSON_URL,
            credentials=ACTIVE_VALUE,
        )

    assert result.exit_code == 0
    assert report(result)["failed_accounts"] == 0
    assert report(result)["succeeded_accounts"] == 1
    state = SnapshotRepository(candidate).load_optional()
    assert state is not None
    assert tuple(account.id for account in state.accounts) == (
        CHANGED_PERSON_URL,
        PERSON_URL,
    )
    assert_posts_only_requests(server)
    assert server.scenario.trigger_calls == 1
    assert server.scenario.progress_calls == 1
    assert server.scenario.download_calls == 1
