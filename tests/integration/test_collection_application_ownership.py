from __future__ import annotations

__test__ = False

import shutil
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
)
from social_media_subscriber.domain import AccountKind
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.snapshot import SnapshotState
from tests.fakes.router import make_account
from tests.integration._collection_application_support import (
    COMPANY_URL,
    PERSON_URL,
    ApplicationClient,
    post,
    request,
    run,
    settings,
    tree,
)

if TYPE_CHECKING:
    from pathlib import Path

    from social_media_subscriber.providers.brightdata.models import BrightDataPost


@pytest.mark.anyio
async def test_same_numeric_changed_slug_preserves_alias_and_record_ownership(
    tmp_path: Path,
) -> None:
    # Given
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    previous = SnapshotRepository(tmp_path / "previous").load_optional()
    assert previous is not None
    original = previous.accounts[0]
    renamed_url = "https://www.linkedin.com/in/synthetic-renamed/"

    # When
    result = await run(
        request(tmp_path, settings(renamed_url), paths=("previous", "renamed")),
        (ApplicationClient(person_posts=(post("activity-2", actor_id="101"),)),),
    )

    # Then
    state = SnapshotRepository(tmp_path / "renamed").load_optional()
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert state is not None
    assert len(state.accounts) == 1
    assert state.accounts[0].profile_url == original.profile_url
    assert state.accounts[0].first_seen_at == original.first_seen_at
    assert state.accounts[0].url_aliases == tuple(sorted((PERSON_URL, renamed_url)))
    assert all(post.account_id == original.id for post in state.posts)
    assert all(source.account_id == original.id for source in state.source_records)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "records",
    [
        (post(actor_id="101"), post("activity-2", actor_id="999")),
        (post().model_copy(update={"user_id": None}),),
        (post().model_copy(update={"use_url": "not-a-linkedin-locator"}),),
        (post().model_copy(update={"use_url": COMPANY_URL}),),
        (post(actor_id="not-numeric"),),
    ],
    ids=("mixed-id", "missing-id", "malformed-actor", "wrong-kind", "nonnumeric"),
)
async def test_adversarial_discovery_abort_preserves_prior_snapshot_bytes(
    tmp_path: Path,
    records: tuple[BrightDataPost, ...],
) -> None:
    # Given
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    prior_bytes = tree(tmp_path / "previous")
    renamed_url = "https://www.linkedin.com/in/synthetic-renamed/"

    # When
    result = await run(
        request(tmp_path, settings(renamed_url), paths=("previous", "rejected")),
        (ApplicationClient(person_posts=records),),
    )

    # Then
    assert result.exit_code is CollectionExitCode.INTEGRITY
    assert result.candidate_change is CandidateChange.ABSENT
    assert tree(tmp_path / "previous") == prior_bytes
    assert not (tmp_path / "rejected").exists()
    assert renamed_url not in repr(result)


@pytest.mark.anyio
async def test_prior_alias_conflict_aborts_before_candidate(tmp_path: Path) -> None:
    # Given
    shared_alias = "https://www.linkedin.com/in/shared-alias/"
    first = make_account(AccountKind.PERSON, 1).model_copy(
        update={"url_aliases": (shared_alias,)}
    )
    second = make_account(AccountKind.PERSON, 2).model_copy(
        update={"url_aliases": (shared_alias,)}
    )
    _ = SnapshotRepository(tmp_path / "previous").write(
        SnapshotState((first, second), (), ())
    )

    # When
    result = await run(
        request(tmp_path, settings(shared_alias)),
        (ApplicationClient(),),
    )

    # Then
    assert result.exit_code is CollectionExitCode.INTEGRITY
    assert not (tmp_path / "candidate").exists()


@pytest.mark.anyio
async def test_accepted_snapshot_failure_is_terminal_partial_without_reroute(
    tmp_path: Path,
) -> None:
    # Given
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    client = ApplicationClient(
        person_failure=BrightDataError(
            BrightDataErrorCategory.SNAPSHOT_TERMINAL,
            snapshot_accepted=True,
        )
    )

    # When
    result = await run(
        request(tmp_path, settings(PERSON_URL), paths=("previous", "partial")),
        (client,),
    )

    # Then
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.candidate_change is CandidateChange.UNCHANGED
    assert [call[0] for call in client.calls] == ["person_posts"]
