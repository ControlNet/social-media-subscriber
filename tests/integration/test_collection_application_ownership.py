from __future__ import annotations

__test__ = False

import shutil
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
)
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.storage.repository import SnapshotRepository
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
async def test_changed_slug_creates_distinct_url_accounts(tmp_path: Path) -> None:
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    renamed_url = "https://www.linkedin.com/in/synthetic-renamed/"
    client = ApplicationClient(
        person_posts=(post("activity-2", actor_url=renamed_url),)
    )

    result = await run(
        request(tmp_path, settings(renamed_url), paths=("previous", "renamed")),
        (client,),
    )

    state = SnapshotRepository(tmp_path / "renamed").load_optional()
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert state is not None
    assert tuple(account.id for account in state.accounts) == tuple(
        sorted((PERSON_URL, renamed_url))
    )
    assert {item.account_id for item in state.posts} == {PERSON_URL, renamed_url}
    assert {item.account_id for item in state.source_records} == {
        PERSON_URL,
        renamed_url,
    }


def _adversarial_records(requested_url: str) -> tuple[tuple[BrightDataPost, ...], ...]:
    malformed = post().model_copy(update={"profile_url": "not-a-linkedin-url"})
    wrong_kind = post().model_copy(update={"profile_url": COMPANY_URL})
    disagree = post().model_copy(
        update={"profile_url": requested_url, "use_url": PERSON_URL}
    )
    cross_owner = post().model_copy(update={"profile_url": PERSON_URL})
    user_id_only = post().model_copy(update={"profile_url": None})
    return ((malformed,), (wrong_kind,), (disagree,), (cross_owner,), (user_id_only,))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "records",
    _adversarial_records("https://www.linkedin.com/in/synthetic-renamed/"),
    ids=("malformed", "wrong_kind", "disagree", "cross_owner", "user_id_only"),
)
async def test_actor_ownership_conflict_preserves_prior_snapshot_bytes(
    tmp_path: Path,
    records: tuple[BrightDataPost, ...],
) -> None:
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    prior = tree(tmp_path / "previous")
    requested_url = "https://www.linkedin.com/in/synthetic-renamed/"

    result = await run(
        request(tmp_path, settings(requested_url), paths=("previous", "rejected")),
        (ApplicationClient(person_posts=records),),
    )

    assert result.exit_code is CollectionExitCode.INTEGRITY
    assert result.candidate_change is CandidateChange.ABSENT
    assert result.failed_account_ids == ()
    assert tree(tmp_path / "previous") == prior
    assert not (tmp_path / "rejected").exists()


@pytest.mark.anyio
async def test_duplicate_post_claimed_by_two_url_owners_is_atomic(
    tmp_path: Path,
) -> None:
    second_url = "https://www.linkedin.com/in/synthetic-second/"
    records = (post(actor_url=PERSON_URL), post(actor_url=second_url))

    result = await run(
        request(tmp_path, settings(PERSON_URL, second_url)),
        (ApplicationClient(person_posts=records),),
    )

    assert result.exit_code is CollectionExitCode.INTEGRITY
    assert result.candidate_change is CandidateChange.ABSENT
    assert not (tmp_path / "candidate").exists()


@pytest.mark.anyio
async def test_accepted_snapshot_failure_is_terminal_without_reroute(
    tmp_path: Path,
) -> None:
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    prior = tree(tmp_path / "previous")
    client = ApplicationClient(
        person_failure=BrightDataError(
            BrightDataErrorCategory.SNAPSHOT_TERMINAL,
            snapshot_accepted=True,
        )
    )

    result = await run(
        request(tmp_path, settings(PERSON_URL), paths=("previous", "partial")),
        (client,),
    )

    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.candidate_change is CandidateChange.UNCHANGED
    assert result.failed_account_ids == (PERSON_URL,)
    assert tree(tmp_path / "partial") == prior
    assert [call[0] for call in client.calls] == ["person_posts"]
