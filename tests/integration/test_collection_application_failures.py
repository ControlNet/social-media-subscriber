from __future__ import annotations

__test__ = False

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
from social_media_subscriber.storage.snapshot import SnapshotManifest, SnapshotState
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
async def test_mixed_known_unknown_validates_discovery_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")

    def reject_write(
        _repository: SnapshotRepository,
        _state: SnapshotState,
    ) -> SnapshotManifest:
        reason = "candidate write occurred before discovery validation"
        raise AssertionError(reason)

    monkeypatch.setattr(SnapshotRepository, "write", reject_write)
    invalid_company_post = post("company-1", actor_id="202").model_copy(
        update={"user_id": None}
    )
    client = ApplicationClient(company_posts=(invalid_company_post,))

    # When
    result = await run(
        request(
            tmp_path,
            settings(PERSON_URL, COMPANY_URL),
            paths=("previous", "integrity"),
        ),
        (client,),
    )

    # Then
    assert result.exit_code is CollectionExitCode.INTEGRITY
    assert result.exit_code.value == 5
    assert result.candidate_change is CandidateChange.ABSENT
    assert not (tmp_path / "integrity").exists()
    assert client.calls == [
        ("person_posts", ((date(2026, 8, 15), date(2026, 8, 20)),)),
        ("company_posts", ((date(2026, 8, 13), date(2026, 8, 20)),)),
    ]


@pytest.mark.anyio
async def test_unknown_unresolved_new_account_is_partial_valid_candidate(
    tmp_path: Path,
) -> None:
    # Given
    client = ApplicationClient(person_posts=())

    # When
    result = await run(request(tmp_path, settings(PERSON_URL)), (client,))

    # Then
    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.failed_accounts == 1
    assert result.failed_account_ids == ()
    assert state is not None
    assert state.accounts == ()
    assert state.posts == ()
    assert state.source_records == ()
    assert client.calls == [("person_posts", ((date(2026, 8, 13), date(2026, 8, 20)),))]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "posts",
    [
        (),
        (post().model_copy(update={"post_type": "repost"}),),
    ],
    ids=("zero", "nonoriginal"),
)
async def test_unknown_zero_or_nonoriginal_is_partial_without_fabricated_identity(
    tmp_path: Path,
    posts: tuple[BrightDataPost, ...],
) -> None:
    # Given
    client = ApplicationClient(person_posts=posts)

    # When
    result = await run(request(tmp_path, settings(PERSON_URL)), (client,))

    # Then
    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.failed_accounts == 1
    assert result.failed_account_ids == ()
    assert state is not None
    assert state == SnapshotState((), (), ())
    assert client.calls == [("person_posts", ((date(2026, 8, 13), date(2026, 8, 20)),))]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        BrightDataError(BrightDataErrorCategory.NOT_FOUND),
        BrightDataError(BrightDataErrorCategory.RETRYABLE),
    ],
)
async def test_isolated_account_failure_preserves_history_and_merges_success(
    tmp_path: Path, failure: BrightDataError
) -> None:
    # Given
    initial = ApplicationClient(company_posts=(post("company-1", actor_id="202"),))
    _ = await run(request(tmp_path, settings(PERSON_URL, COMPANY_URL)), (initial,))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    partial = ApplicationClient(
        person_posts=(post(text="Edited"),),
        company_failure=failure,
    )

    # When
    result = await run(
        request(
            tmp_path,
            settings(PERSON_URL, COMPANY_URL),
            paths=("previous", "partial"),
        ),
        (partial,),
    )

    # Then
    state = SnapshotRepository(tmp_path / "partial").load_optional()
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.candidate_change is CandidateChange.CHANGED
    assert result.failed_accounts == 1
    assert state is not None
    assert len(state.accounts) == len(state.posts) == 2


@pytest.mark.anyio
async def test_partial_failure_can_produce_unchanged_candidate(tmp_path: Path) -> None:
    # Given
    initial = ApplicationClient(company_posts=(post("company-1", actor_id="202"),))
    _ = await run(request(tmp_path, settings(PERSON_URL, COMPANY_URL)), (initial,))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    prior = tree(tmp_path / "previous")
    partial = ApplicationClient(
        company_failure=BrightDataError(BrightDataErrorCategory.NOT_FOUND)
    )

    # When
    result = await run(
        request(
            tmp_path,
            settings(PERSON_URL, COMPANY_URL),
            paths=("previous", "partial"),
        ),
        (partial,),
    )

    # Then
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.candidate_change is CandidateChange.UNCHANGED
    assert tree(tmp_path / "partial") == prior


@pytest.mark.anyio
async def test_total_pool_failure_writes_no_candidate(tmp_path: Path) -> None:
    # Given
    clients = (
        ApplicationClient(
            person_failure=BrightDataError(BrightDataErrorCategory.QUOTA)
        ),
        ApplicationClient(person_failure=BrightDataError(BrightDataErrorCategory.AUTH)),
    )

    # When
    result = await run(
        request(tmp_path, settings(PERSON_URL, keys="synthetic-one\nsynthetic-two")),
        clients,
    )

    # Then
    assert result.exit_code is CollectionExitCode.PROVIDER
    assert result.candidate_change is CandidateChange.ABSENT
    assert not (tmp_path / "candidate").exists()
    assert [call[0] for client in clients for call in client.calls] == [
        "person_posts",
        "person_posts",
    ]


@pytest.mark.anyio
async def test_schema_abort_writes_no_candidate(tmp_path: Path) -> None:
    # Given
    invalid_post = post().model_copy(update={"user_id": None})
    client = ApplicationClient(person_posts=(invalid_post,))

    # When
    result = await run(request(tmp_path, settings(PERSON_URL)), (client,))

    # Then
    assert result.exit_code is CollectionExitCode.INTEGRITY
    assert not (tmp_path / "candidate").exists()
