from __future__ import annotations

__test__ = False

import shutil
from datetime import date
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.application import collect as collect_module
from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
)
from social_media_subscriber.storage.merge import merge_snapshot
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

    from social_media_subscriber.storage.snapshot import SnapshotState, SnapshotSummary


@pytest.mark.anyio
async def test_all_success_new_run_writes_valid_candidate(tmp_path: Path) -> None:
    # Given
    client = ApplicationClient()

    # When
    result = await run(request(tmp_path, settings(PERSON_URL)), (client,))

    # Then
    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert result.candidate_change is CandidateChange.CHANGED
    assert result.digest is not None
    assert state is not None
    assert (len(state.accounts), len(state.posts)) == (1, 1)
    assert client.calls[-1] == (
        "person_posts",
        ((date(2003, 5, 5), date(2026, 8, 20)),),
    )


@pytest.mark.anyio
async def test_posts_first_unknown_uses_posts_without_identity_lookup(
    tmp_path: Path,
) -> None:
    # Given
    client = ApplicationClient()

    # When
    result = await run(request(tmp_path, settings(PERSON_URL)), (client,))

    # Then
    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert result.failed_accounts == 0
    assert state is not None
    assert (len(state.accounts), len(state.posts)) == (1, 1)
    assert client.calls == [("person_posts", ((date(2003, 5, 5), date(2026, 8, 20)),))]


@pytest.mark.anyio
async def test_posts_first_unknown_respects_explicit_window(tmp_path: Path) -> None:
    # Given
    client = ApplicationClient()

    # When
    result = await run(
        request(
            tmp_path,
            settings(PERSON_URL),
            window=(date(2026, 7, 1), date(2026, 7, 2)),
        ),
        (client,),
    )

    # Then
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert client.calls == [("person_posts", ((date(2026, 7, 1), date(2026, 7, 2)),))]


@pytest.mark.anyio
async def test_overlap_rerun_is_byte_identical_no_change(tmp_path: Path) -> None:
    # Given
    first = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    before = tree(tmp_path / "previous")
    client = ApplicationClient()

    # When
    second = await run(
        request(tmp_path, settings(PERSON_URL), paths=("previous", "second")),
        (client,),
    )

    # Then
    assert first.exit_code is second.exit_code is CollectionExitCode.SUCCESS
    assert second.candidate_change is CandidateChange.UNCHANGED
    assert tree(tmp_path / "second") == before
    assert client.calls == [("person_posts", ((date(2026, 8, 17), date(2026, 8, 20)),))]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("client", "expected_text", "expected_likes"),
    [
        (ApplicationClient(person_posts=(post(text="Edited"),)), "Edited", 1),
        (ApplicationClient(person_posts=(post(likes=9),)), "Synthetic post", 9),
    ],
)
async def test_post_or_source_only_change_updates_candidate(
    tmp_path: Path,
    client: ApplicationClient,
    expected_text: str,
    expected_likes: int,
) -> None:
    # Given
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")

    # When
    result = await run(
        request(tmp_path, settings(PERSON_URL), paths=("previous", "changed")),
        (client,),
    )

    # Then
    state = SnapshotRepository(tmp_path / "changed").load_optional()
    assert result.candidate_change is CandidateChange.CHANGED
    assert state is not None
    assert state.posts[0].content["text"] == expected_text
    assert state.posts[0].content["engagement"] == {"likes": expected_likes}


@pytest.mark.anyio
async def test_known_zero_posts_preserves_history(tmp_path: Path) -> None:
    # Given
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    prior = tree(tmp_path / "previous")
    zero_client = ApplicationClient(person_posts=())

    # When
    result = await run(
        request(tmp_path, settings(PERSON_URL), paths=("previous", "zero")),
        (zero_client,),
    )

    # Then
    state = SnapshotRepository(tmp_path / "zero").load_optional()
    assert result.candidate_change is CandidateChange.UNCHANGED
    assert state is not None
    assert len(state.posts) == 1
    assert tree(tmp_path / "zero") == prior
    assert zero_client.calls == [
        ("person_posts", ((date(2026, 8, 17), date(2026, 8, 20)),))
    ]


@pytest.mark.anyio
async def test_mixed_existing_and_new_urls_use_incremental_and_initial_windows(
    tmp_path: Path,
) -> None:
    # Given
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    client = ApplicationClient(
        company_posts=(post("company-1", actor_url=COMPANY_URL),)
    )

    # When
    result = await run(
        request(
            tmp_path,
            settings(PERSON_URL, COMPANY_URL),
            paths=("previous", "mixed"),
        ),
        (client,),
    )

    # Then
    state = SnapshotRepository(tmp_path / "mixed").load_optional()
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert state is not None
    assert (len(state.accounts), len(state.posts)) == (2, 2)
    assert {post.account_id for post in state.posts} == {
        account.id for account in state.accounts
    }
    assert client.calls == [
        ("person_posts", ((date(2026, 8, 17), date(2026, 8, 20)),)),
        ("company_posts", ((date(2003, 5, 5), date(2026, 8, 20)),)),
    ]


@pytest.mark.anyio
async def test_mixed_known_unknown_merges_and_writes_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    original_merge = merge_snapshot
    original_write = SnapshotRepository.write
    merge_calls = 0
    write_calls = 0

    def track_merge(
        previous: SnapshotState | None,
        current: SnapshotState,
    ) -> SnapshotState:
        nonlocal merge_calls
        merge_calls += 1
        return original_merge(previous, current)

    def track_write(
        repository: SnapshotRepository,
        state: SnapshotState,
    ) -> SnapshotSummary:
        nonlocal write_calls
        write_calls += 1
        return original_write(repository, state)

    monkeypatch.setattr(collect_module, "merge_snapshot", track_merge)
    monkeypatch.setattr(SnapshotRepository, "write", track_write)
    client = ApplicationClient(
        company_posts=(post("company-1", actor_url=COMPANY_URL),)
    )

    # When
    result = await run(
        request(
            tmp_path,
            settings(PERSON_URL, COMPANY_URL),
            paths=("previous", "single-write"),
        ),
        (client,),
    )

    # Then
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert merge_calls == write_calls == 1
    assert client.calls == [
        ("person_posts", ((date(2026, 8, 17), date(2026, 8, 20)),)),
        ("company_posts", ((date(2003, 5, 5), date(2026, 8, 20)),)),
    ]
