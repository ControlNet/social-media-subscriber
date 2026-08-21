from __future__ import annotations

from typing import TYPE_CHECKING

from tests.e2e.brightdata_server import (
    ACTIVE_VALUE,
    CHANGED_PERSON_URL,
    MEDIA_CANARY,
    OWNERSHIP_CANARY,
    PERSON_URL,
    REVOKED_VALUE,
    SCHEMA_CANARY,
    FakeBrightDataServer,
    PersonPostScenario,
)
from tests.e2e.pipeline_harness import invoke_collect, report, tree

if TYPE_CHECKING:
    from pathlib import Path


def assert_accepted_snapshot_and_ownership_failures_do_not_leak(tmp_path: Path) -> None:
    accepted = FakeBrightDataServer()
    accepted.scenario.accepted_snapshot_failure = True
    ownership = FakeBrightDataServer()
    ownership.scenario.person_result = PersonPostScenario.OWNERSHIP_CONFLICT

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
    assert accepted.scenario.scrape_calls == 0
    assert ownership.scenario.scrape_calls == 0
    assert not accepted.thread_alive
    assert not ownership.thread_alive
    assert unused.scenario.requests == []
    assert report(owned)["failed_account_ids"] == [PERSON_URL]
    assert report(conflict)["failed_account_ids"] == []
    assert report(malformed)["failed_account_ids"] == []
    combined = "\n".join(
        line
        for result in (owned, conflict, malformed)
        for line in result.output.splitlines()
        if not line.startswith("{")
    )
    assert REVOKED_VALUE not in combined
    assert ACTIVE_VALUE not in combined
    assert MEDIA_CANARY not in combined
    assert OWNERSHIP_CANARY not in combined
    assert PERSON_URL not in combined


def assert_invalid_schema_aborts_without_candidate_or_leak(tmp_path: Path) -> None:
    previous = tmp_path / "previous"
    candidate = tmp_path / "candidate"
    with FakeBrightDataServer() as initial_server:
        assert (
            invoke_collect(initial_server, tmp_path / "absent", previous).exit_code == 0
        )
    before = tree(previous)
    server = FakeBrightDataServer()
    server.scenario.person_result = PersonPostScenario.INVALID_SCHEMA

    with server:
        result = invoke_collect(
            server,
            previous,
            candidate,
            accounts=CHANGED_PERSON_URL,
            credentials=ACTIVE_VALUE,
        )

    assert result.exit_code == 5
    assert not candidate.exists()
    assert tree(previous) == before
    assert not server.thread_alive
    assert [request.endpoint for request in server.scenario.requests] == ["trigger"]
    assert {request.dataset for request in server.scenario.requests} == {
        "gd_lyy3tktm25m4avu764"
    }
    assert server.scenario.trigger_calls == 1
    assert server.scenario.progress_calls == 0
    assert server.scenario.download_calls == 0
    assert server.scenario.scrape_calls == 0
    assert ACTIVE_VALUE not in result.output
    assert PERSON_URL not in result.output
    assert CHANGED_PERSON_URL not in result.output
    assert SCHEMA_CANARY not in result.output
    assert MEDIA_CANARY not in result.output
