from __future__ import annotations

import shutil
from datetime import date
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
@pytest.mark.parametrize(
    "posts",
    [(), (post().model_copy(update={"post_type": "repost"}),)],
    ids=("zero_records", "non_original_only"),
)
async def test_success_without_original_posts_persists_url_account(
    tmp_path: Path,
    posts: tuple[BrightDataPost, ...],
) -> None:
    client = ApplicationClient(person_posts=posts)

    result = await run(request(tmp_path, settings(PERSON_URL)), (client,))

    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert result.candidate_change is CandidateChange.CHANGED
    assert result.succeeded_accounts == 1
    assert result.failed_accounts == 0
    assert result.failed_account_ids == ()
    assert state is not None
    assert tuple(account.profile_url for account in state.accounts) == (PERSON_URL,)
    assert state.posts == ()
    assert state.source_records == ()
    assert client.calls == [("person_posts", ((date(2026, 8, 13), date(2026, 8, 20)),))]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "category",
    [BrightDataErrorCategory.INPUT, BrightDataErrorCategory.NOT_FOUND],
    ids=("typed_input", "typed_not_found"),
)
async def test_typed_failure_does_not_create_new_url_account(
    tmp_path: Path, category: BrightDataErrorCategory
) -> None:
    client = ApplicationClient(person_failure=BrightDataError(category))

    result = await run(request(tmp_path, settings(PERSON_URL)), (client,))

    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.failed_accounts == 1
    assert result.failed_account_ids == (PERSON_URL,)
    assert state is not None
    assert state.accounts == state.posts == state.source_records == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "category",
    [BrightDataErrorCategory.INPUT, BrightDataErrorCategory.NOT_FOUND],
    ids=("typed_input", "typed_not_found"),
)
async def test_typed_failure_preserves_existing_url_history(
    tmp_path: Path, category: BrightDataErrorCategory
) -> None:
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    prior = tree(tmp_path / "previous")
    client = ApplicationClient(person_failure=BrightDataError(category))

    result = await run(
        request(tmp_path, settings(PERSON_URL), paths=("previous", "failed")),
        (client,),
    )

    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.candidate_change is CandidateChange.UNCHANGED
    assert result.failed_account_ids == (PERSON_URL,)
    assert tree(tmp_path / "failed") == prior


@pytest.mark.anyio
@pytest.mark.parametrize(
    "category",
    [BrightDataErrorCategory.INPUT, BrightDataErrorCategory.NOT_FOUND],
    ids=("typed_input", "typed_not_found"),
)
async def test_n8_typed_not_found_preserves_history_and_attributes_url(
    tmp_path: Path, category: BrightDataErrorCategory
) -> None:
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    prior = tree(tmp_path / "previous")
    raw_not_found_url = (
        "https://de.linkedin.com/in/synthetic-not-found?source=synthetic"
    )
    synthetic_not_found_url = "https://www.linkedin.com/in/synthetic-not-found/"
    client = ApplicationClient(person_failure=BrightDataError(category))

    result = await run(
        request(
            tmp_path,
            settings(raw_not_found_url),
            paths=("previous", "failed"),
        ),
        (client,),
    )

    state = SnapshotRepository(tmp_path / "failed").load_optional()
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.candidate_change is CandidateChange.UNCHANGED
    assert result.succeeded_accounts == 0
    assert result.failed_accounts == 1
    assert result.failed_account_ids == (synthetic_not_found_url,)  # RED-PROBE-T7
    assert state is not None
    assert tuple(account.profile_url for account in state.accounts) == (PERSON_URL,)
    assert all(
        account.profile_url != synthetic_not_found_url for account in state.accounts
    )
    assert tree(tmp_path / "failed") == prior


@pytest.mark.anyio
async def test_isolated_failure_preserves_history_and_merges_success(
    tmp_path: Path,
) -> None:
    initial = ApplicationClient(
        company_posts=(post("company-1", actor_url=COMPANY_URL),)
    )
    _ = await run(request(tmp_path, settings(PERSON_URL, COMPANY_URL)), (initial,))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    partial = ApplicationClient(
        person_posts=(post(text="Edited"),),
        company_failure=BrightDataError(BrightDataErrorCategory.NOT_FOUND),
    )

    result = await run(
        request(
            tmp_path,
            settings(PERSON_URL, COMPANY_URL),
            paths=("previous", "partial"),
        ),
        (partial,),
    )

    state = SnapshotRepository(tmp_path / "partial").load_optional()
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.candidate_change is CandidateChange.CHANGED
    assert result.failed_account_ids == (COMPANY_URL,)
    assert state is not None
    assert len(state.accounts) == len(state.posts) == 2


@pytest.mark.anyio
async def test_total_pool_failure_writes_no_candidate_and_attributes_url(
    tmp_path: Path,
) -> None:
    clients = (
        ApplicationClient(
            person_failure=BrightDataError(BrightDataErrorCategory.QUOTA)
        ),
        ApplicationClient(person_failure=BrightDataError(BrightDataErrorCategory.AUTH)),
    )

    result = await run(
        request(tmp_path, settings(PERSON_URL, keys="synthetic-one\nsynthetic-two")),
        clients,
    )

    assert result.exit_code is CollectionExitCode.PROVIDER
    assert result.candidate_change is CandidateChange.ABSENT
    assert result.failed_account_ids == (PERSON_URL,)
    assert not (tmp_path / "candidate").exists()


@pytest.mark.anyio
async def test_schema_abort_writes_no_candidate(tmp_path: Path) -> None:
    user_id_only = post().model_copy(update={"profile_url": None})

    result = await run(
        request(tmp_path, settings(PERSON_URL)),
        (ApplicationClient(person_posts=(user_id_only,)),),
    )

    assert result.exit_code is CollectionExitCode.INTEGRITY
    assert result.failed_account_ids == ()
    assert not (tmp_path / "candidate").exists()
