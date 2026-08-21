from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from social_media_subscriber.domain.ids import record_filename
from social_media_subscriber.providers.brightdata.constants import (
    LINKEDIN_POSTS_DATASET,
)
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
from social_media_subscriber.serialization.json import read_json
from social_media_subscriber.storage.layout import (
    ACCOUNTS_INDEX,
    FEED_INDEX,
    MANIFEST,
    snapshot_digest,
)
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.snapshot import (
    AccountsIndex,
    FeedIndex,
    SnapshotManifest,
    SnapshotState,
)
from tests.e2e.brightdata_server import (
    ACTIVE_VALUE,
    CHANGED_PERSON_URL,
    MEDIA_CANARY,
    PERSON_URL,
    REVOKED_VALUE,
    FakeBrightDataServer,
    PersonPostScenario,
)
from tests.e2e.brightdata_server_fixtures import PERSON_FEED_IDS
from tests.e2e.pipeline_harness import invoke_collect, report, tree

if TYPE_CHECKING:
    from collections.abc import Sequence


def assert_snapshot_metadata(
    root: Path,
    *,
    account_urls: Sequence[str],
    feed_ids: tuple[str, ...],
) -> tuple[SnapshotState, SnapshotManifest]:
    state = SnapshotRepository(root).load_optional()
    assert state is not None
    accounts_index = read_json(root / ACCOUNTS_INDEX, AccountsIndex)
    feed_index = read_json(root / FEED_INDEX, FeedIndex)
    manifest = read_json(root / MANIFEST, SnapshotManifest)
    expected_accounts = {
        str(account.id): f"accounts/{record_filename(account.id)}"
        for account in state.accounts
    }
    assert tuple(account.id for account in state.accounts) == tuple(account_urls)
    assert accounts_index.accounts == expected_accounts
    assert feed_index.posts == feed_ids
    non_manifest = {
        Path(relative): payload
        for relative, payload in tree(root).items()
        if relative != MANIFEST.as_posix()
    }
    assert manifest.digest == snapshot_digest(non_manifest)
    return state, manifest


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
    ) == (1, 3, 3)
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
    assert len(list((candidate / "accounts").glob("*.json"))) == 1
    assert len(list((candidate / "posts/linkedin").glob("*.json"))) == 3
    source_records = [
        read_json(path, BrightDataLinkedInPostSourceRecord)
        for path in (candidate / "source").rglob("*.json")
    ]
    assert all(record.account_id == PERSON_URL for record in source_records)
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
    state, manifest = assert_snapshot_metadata(
        renamed,
        account_urls=(CHANGED_PERSON_URL, PERSON_URL),
        feed_ids=PERSON_FEED_IDS,
    )
    assert all(account.id == account.profile_url for account in state.accounts)
    assert_posts_only_requests(initial_server)
    assert_posts_only_requests(server)
    assert [request.endpoint for request in server.scenario.requests] == [
        "trigger",
        "progress",
        "download",
    ]
    assert report(result)["digest"] == manifest.digest
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

    manifest = read_json(candidate / "snapshot.json", SnapshotManifest)
    assert result.exit_code == 0
    assert report(result)["failed_accounts"] == 0
    assert report(result)["succeeded_accounts"] == 1
    assert (
        manifest.account_count,
        manifest.post_count,
        manifest.source_record_count,
    ) == (1, 0, 0)
    state = SnapshotRepository(candidate).load_optional()
    assert state is not None
    assert tuple(account.id for account in state.accounts) == (PERSON_URL,)
    assert not list((candidate / "posts/linkedin").glob("*.json"))
    assert not list((candidate / "source").rglob("*.json"))
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
