from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest
from pydantic import TypeAdapter

from social_media_subscriber.storage.repository import SnapshotRepository
from tests.e2e.brightdata_server import (
    ACTIVE_VALUE,
    COMPANY_URL,
    MEDIA_CANARY,
    OWNERSHIP_CANARY,
    PERSON_URL,
    REVOKED_VALUE,
    FakeBrightDataServer,
    PersonPostScenario,
)
from tests.e2e.brightdata_server_fixtures import PERSON_FEED_IDS
from tests.e2e.pipeline_harness import (
    NOT_FOUND_PERSON_URL,
    invoke_collect,
    report,
    tree,
)
from tests.e2e.posts_first_scenarios import assert_snapshot_metadata

_DEAD_PROXY: Final = "http://127.0.0.1:9"
_DRIVER_REPORT: Final = TypeAdapter(dict[str, str | int | list[str] | None])


def test_cli_success_collects_person_and_company_over_loopback(tmp_path: Path) -> None:
    server = FakeBrightDataServer()
    with server:
        result = invoke_collect(
            server,
            tmp_path / "absent",
            tmp_path / "candidate",
            accounts=f"{PERSON_URL}\n{COMPANY_URL}",
            credentials=ACTIVE_VALUE,
        )

    state, manifest = assert_snapshot_metadata(
        tmp_path / "candidate",
        account_urls=(COMPANY_URL, PERSON_URL),
        feed_ids=(*PERSON_FEED_IDS, "linkedin:post:urn:li:activity:2001"),
    )
    assert result.exit_code == 0
    assert report(result) == {
        "candidate_change": "changed",
        "command": "collect",
        "digest": manifest.digest,
        "exit_code": 0,
        "failed_account_ids": [],
        "failed_accounts": 0,
        "succeeded_accounts": 2,
    }
    assert len(state.posts) == len(state.source_records) == 4
    assert all(account.schema_version == 2 for account in state.accounts)
    assert all(post.schema_version == 2 for post in state.posts)
    assert all(source.schema_version == 2 for source in state.source_records)
    assert [request.discovery for request in server.scenario.requests] == [
        "profile_url",
        None,
        None,
        "company_url",
        None,
        None,
    ]
    assert not server.thread_alive


@pytest.mark.parametrize(
    "person_result",
    [PersonPostScenario.ZERO, PersonPostScenario.NONORIGINAL_ONLY],
    ids=["zero", "non_original"],
)
def test_cli_zero_or_non_original_persists_url_only(
    tmp_path: Path,
    person_result: PersonPostScenario,
) -> None:
    server = FakeBrightDataServer()
    server.scenario.person_result = person_result
    with server:
        result = invoke_collect(
            server,
            tmp_path / "absent",
            tmp_path / "candidate",
            credentials=ACTIVE_VALUE,
        )

    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert state is not None
    assert result.exit_code == 0
    assert tuple(str(account.id) for account in state.accounts) == (PERSON_URL,)
    assert state.accounts[0].schema_version == 2
    assert state.posts == state.source_records == ()
    assert report(result)["failed_account_ids"] == []
    assert not server.thread_alive


def test_cli_not_found_reports_canonical_failed_url(tmp_path: Path) -> None:
    synthetic_url = PERSON_URL
    server = FakeBrightDataServer()
    server.scenario.fail_person_posts = True
    with server:
        result = invoke_collect(
            server,
            tmp_path / "absent",
            tmp_path / "candidate",
            accounts=synthetic_url,
            credentials=ACTIVE_VALUE,
        )

    report_value = report(result)
    assert result.exit_code == 4
    assert report_value["failed_account_ids"] == [synthetic_url]  # RED-PROBE-T12
    assert report_value["failed_accounts"] == 1
    assert report_value["succeeded_accounts"] == 0
    assert not server.thread_alive


def test_cli_conflict_is_redacted_and_preserves_previous_bytes(tmp_path: Path) -> None:
    previous = tmp_path / "previous"
    with FakeBrightDataServer() as initial:
        assert invoke_collect(initial, tmp_path / "absent", previous).exit_code == 0
    before = tree(previous)
    server = FakeBrightDataServer()
    server.scenario.person_result = PersonPostScenario.OWNERSHIP_CONFLICT
    with server:
        result = invoke_collect(
            server,
            previous,
            tmp_path / "candidate",
            credentials=ACTIVE_VALUE,
        )

    assert result.exit_code == 5
    assert not (tmp_path / "candidate").exists()
    assert tree(previous) == before
    assert report(result)["failed_account_ids"] == []
    for canary in (ACTIVE_VALUE, OWNERSHIP_CANARY, MEDIA_CANARY, PERSON_URL):
        assert canary not in result.output
    assert not server.thread_alive


def test_cli_failover_uses_real_client_and_redacts_credentials(tmp_path: Path) -> None:
    server = FakeBrightDataServer()
    with server:
        result = invoke_collect(server, tmp_path / "absent", tmp_path / "candidate")

    assert result.exit_code == 0
    assert [request.credential for request in server.scenario.requests] == [
        "revoked",
        "revoked",
        "revoked",
        "active",
        "active",
        "active",
    ]
    assert REVOKED_VALUE not in result.output
    assert ACTIVE_VALUE not in result.output
    assert MEDIA_CANARY not in result.output
    assert not server.thread_alive


def test_cli_rollback_keeps_previous_snapshot_on_accepted_failure(
    tmp_path: Path,
) -> None:
    previous = tmp_path / "previous"
    with FakeBrightDataServer() as initial:
        assert invoke_collect(initial, tmp_path / "absent", previous).exit_code == 0
    before = tree(previous)
    server = FakeBrightDataServer()
    server.scenario.accepted_snapshot_failure = True
    with server:
        result = invoke_collect(server, previous, tmp_path / "candidate")

    assert result.exit_code == 4
    assert tree(previous) == before
    assert tree(tmp_path / "candidate") == before
    assert report(result)["failed_account_ids"] == [PERSON_URL]
    assert not server.thread_alive


@pytest.mark.parametrize(
    ("scenario", "expected_exit", "succeeded", "failed_ids"),
    [
        ("success", 0, 2, []),
        ("not-found", 4, 0, [NOT_FOUND_PERSON_URL]),
    ],
)
def test_contained_driver_emits_exactly_one_json_line(
    tmp_path: Path,
    scenario: str,
    expected_exit: int,
    succeeded: int,
    failed_ids: list[str],
) -> None:
    environment = os.environ.copy()
    environment.update(
        HTTP_PROXY=_DEAD_PROXY,
        HTTPS_PROXY=_DEAD_PROXY,
        ALL_PROXY=_DEAD_PROXY,
        NO_PROXY="127.0.0.1,localhost",
        ACCOUNTS="",
        BRIGHT_DATA_API_KEYS="",
    )
    completed = subprocess.run(  # noqa: S603 - exact Pixi interpreter, fixed module
        [
            sys.executable,
            "-m",
            "tests.e2e.run_contained_cli",
            "--scenario",
            scenario,
            "--root",
            str(tmp_path / scenario),
        ],
        cwd=Path(__file__).parents[2],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    lines = completed.stdout.splitlines()
    assert completed.returncode == expected_exit
    assert len(lines) == 1
    driver_report = _DRIVER_REPORT.validate_json(lines[0])
    assert driver_report["exit_code"] == expected_exit
    assert driver_report["succeeded_accounts"] == succeeded
    assert driver_report["failed_account_ids"] == failed_ids
    assert completed.stderr == ""
