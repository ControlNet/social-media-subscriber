from __future__ import annotations

from typing import TYPE_CHECKING

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
    PERSON_URL,
    REVOKED_VALUE,
    FakeBrightDataServer,
    PersonPostScenario,
)
from tests.e2e.pipeline_harness import invoke_collect, report, tree

if TYPE_CHECKING:
    from pathlib import Path


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


def assert_changed_slug_reconciles_same_numeric_account(tmp_path: Path) -> None:
    first = tmp_path / "first"
    renamed = tmp_path / "renamed"
    with FakeBrightDataServer() as initial_server:
        assert invoke_collect(initial_server, tmp_path / "absent", first).exit_code == 0
    server = FakeBrightDataServer()
    server.scenario.person_actor_url = CHANGED_PERSON_URL

    with server:
        result = invoke_collect(
            server,
            first,
            renamed,
            accounts=CHANGED_PERSON_URL,
            credentials=ACTIVE_VALUE,
        )

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


def assert_empty_result_preserves_prior_tree(
    tmp_path: Path, person_result: PersonPostScenario
) -> None:
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

    with server:
        result = invoke_collect(
            server,
            baseline,
            candidate,
            accounts=CHANGED_PERSON_URL,
            credentials=ACTIVE_VALUE,
        )

    assert result.exit_code == 4
    assert report(result)["failed_accounts"] == 1
    assert report(result)["succeeded_accounts"] == 0
    assert SnapshotRepository(candidate).load_optional() is not None
    assert tree(candidate) == before
    assert server.scenario.identity_calls == server.scenario.scrape_calls == 0
    assert server.scenario.trigger_calls == 1
    assert server.scenario.progress_calls == 1
    assert server.scenario.download_calls == 1
