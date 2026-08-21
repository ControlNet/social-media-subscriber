from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from social_media_subscriber.adapters.router_outcomes import RouterRunStatus
from social_media_subscriber.application.collect import (
    CollectionRequest,
    collect_snapshot,
)
from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
)
from social_media_subscriber.domain import AccountKind
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.providers.brightdata.models import (
    BrightDataCompanyIdentity,
    BrightDataPersonIdentity,
    BrightDataPost,
)
from social_media_subscriber.settings import Settings
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
from social_media_subscriber.storage.snapshot import SnapshotState
from tests.fakes.router import make_account

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from social_media_subscriber.application.results import CollectionResult
    from social_media_subscriber.providers.brightdata.requests import (
        PostDiscoveryInput,
    )

RUN_START = datetime(2026, 8, 20, 12, tzinfo=UTC)
PERSON_URL = "https://www.linkedin.com/in/synthetic-person/"
COMPANY_URL = "https://www.linkedin.com/company/synthetic-company/"


def _settings(*urls: str, keys: str = "synthetic-key") -> Settings:
    return Settings(
        accounts=SecretStr("\n".join(urls)),
        bright_data_api_keys=SecretStr(keys),
    )


def _post(
    post_id: str = "activity-1",
    *,
    actor_id: str = "101",
    text: str = "Synthetic post",
    likes: int = 1,
) -> BrightDataPost:
    return BrightDataPost.model_validate(
        {
            "id": post_id,
            "date_posted": "2026-08-18T12:00:00+00:00",
            "post_type": "post",
            "url": f"https://www.linkedin.com/posts/{post_id}/",
            "user_id": actor_id,
            "post_text": text,
            "num_likes": likes,
        }
    )


@dataclass(slots=True)
class ApplicationClient:
    person_identity: BrightDataPersonIdentity | None = field(
        default_factory=lambda: BrightDataPersonIdentity(
            linkedin_num_id="101", url=PERSON_URL
        )
    )
    company_identity: BrightDataCompanyIdentity | None = field(
        default_factory=lambda: BrightDataCompanyIdentity(
            company_id="202", url=COMPANY_URL
        )
    )
    person_posts: tuple[BrightDataPost, ...] = field(default_factory=lambda: (_post(),))
    company_posts: tuple[BrightDataPost, ...] = ()
    person_failure: BrightDataError | None = None
    company_failure: BrightDataError | None = None
    calls: list[tuple[str, tuple[tuple[date, date], ...]]] = field(default_factory=list)
    close_calls: int = 0

    async def aclose(self) -> None:
        self.close_calls += 1

    async def resolve_person_identities(
        self, urls: Sequence[str]
    ) -> tuple[BrightDataPersonIdentity, ...]:
        _ = urls
        self.calls.append(("person_identity", ()))
        if self.person_failure is not None:
            raise self.person_failure
        return () if self.person_identity is None else (self.person_identity,)

    async def resolve_company_identities(
        self, urls: Sequence[str]
    ) -> tuple[BrightDataCompanyIdentity, ...]:
        _ = urls
        self.calls.append(("company_identity", ()))
        if self.company_failure is not None:
            raise self.company_failure
        return () if self.company_identity is None else (self.company_identity,)

    async def collect_person_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        self.calls.append(
            ("person_posts", tuple((item.start_date, item.end_date) for item in inputs))
        )
        if self.person_failure is not None:
            raise self.person_failure
        return self.person_posts

    async def collect_company_posts(
        self, inputs: Sequence[PostDiscoveryInput]
    ) -> tuple[BrightDataPost, ...]:
        self.calls.append(
            (
                "company_posts",
                tuple((item.start_date, item.end_date) for item in inputs),
            )
        )
        if self.company_failure is not None:
            raise self.company_failure
        return self.company_posts


def _request(
    root: Path,
    settings: Settings,
    *,
    paths: tuple[str, str] = ("previous", "candidate"),
    window: tuple[date | None, date | None] = (None, None),
) -> CollectionRequest:
    return CollectionRequest(
        settings=settings,
        previous_snapshot_dir=root / paths[0],
        candidate_snapshot_dir=root / paths[1],
        run_started_at=RUN_START,
        start_date=window[0],
        end_date=window[1],
    )


async def _run(
    request: CollectionRequest,
    clients: tuple[ApplicationClient, ...],
) -> CollectionResult:
    remaining = iter(clients)
    return await collect_snapshot(request, lambda _credential: next(remaining))


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.anyio
async def test_all_success_new_run_writes_valid_candidate(tmp_path: Path) -> None:
    # Given
    client = ApplicationClient()

    # When
    result = await _run(_request(tmp_path, _settings(PERSON_URL)), (client,))

    # Then
    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert result.candidate_change is CandidateChange.CHANGED
    assert result.digest is not None
    assert state is not None
    assert (len(state.accounts), len(state.posts), len(state.source_records)) == (
        1,
        1,
        1,
    )
    assert client.calls[-1] == (
        "person_posts",
        ((date(2026, 8, 13), date(2026, 8, 20)),),
    )


@pytest.mark.anyio
async def test_overlap_rerun_is_byte_identical_no_change(tmp_path: Path) -> None:
    # Given
    first = await _run(
        _request(tmp_path, _settings(PERSON_URL)), (ApplicationClient(),)
    )
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    before = _tree(tmp_path / "previous")
    client = ApplicationClient()

    # When
    second = await _run(
        _request(tmp_path, _settings(PERSON_URL), paths=("previous", "second")),
        (client,),
    )

    # Then
    assert first.exit_code is second.exit_code is CollectionExitCode.SUCCESS
    assert second.candidate_change is CandidateChange.UNCHANGED
    assert _tree(tmp_path / "second") == before
    assert client.calls == [("person_posts", ((date(2026, 8, 15), date(2026, 8, 20)),))]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("client", "expected_text", "expected_likes"),
    [
        (ApplicationClient(person_posts=(_post(text="Edited"),)), "Edited", 1),
        (ApplicationClient(person_posts=(_post(likes=9),)), "Synthetic post", 9),
    ],
)
async def test_post_or_source_only_change_updates_candidate(
    tmp_path: Path,
    client: ApplicationClient,
    expected_text: str,
    expected_likes: int,
) -> None:
    # Given
    _ = await _run(_request(tmp_path, _settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")

    # When
    result = await _run(
        _request(tmp_path, _settings(PERSON_URL), paths=("previous", "changed")),
        (client,),
    )

    # Then
    state = SnapshotRepository(tmp_path / "changed").load_optional()
    assert result.candidate_change is CandidateChange.CHANGED
    assert state is not None
    assert state.posts[0].text == expected_text
    assert state.source_records[0].payload["num_likes"] == expected_likes


@pytest.mark.anyio
async def test_known_zero_posts_preserves_history(tmp_path: Path) -> None:
    # Given
    _ = await _run(_request(tmp_path, _settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")

    # When
    result = await _run(
        _request(tmp_path, _settings(PERSON_URL), paths=("previous", "zero")),
        (ApplicationClient(person_posts=()),),
    )

    # Then
    state = SnapshotRepository(tmp_path / "zero").load_optional()
    assert result.candidate_change is CandidateChange.UNCHANGED
    assert state is not None
    assert len(state.posts) == len(state.source_records) == 1


@pytest.mark.anyio
async def test_unresolved_new_account_is_partial_valid_candidate(
    tmp_path: Path,
) -> None:
    # Given
    client = ApplicationClient(person_identity=BrightDataPersonIdentity())

    # When
    result = await _run(_request(tmp_path, _settings(PERSON_URL)), (client,))

    # Then
    state = SnapshotRepository(tmp_path / "candidate").load_optional()
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.failed_accounts == 1
    assert result.failed_account_ids == ()
    assert state is not None
    assert state.accounts == ()


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
    initial = ApplicationClient(company_posts=(_post("company-1", actor_id="202"),))
    _ = await _run(_request(tmp_path, _settings(PERSON_URL, COMPANY_URL)), (initial,))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    partial = ApplicationClient(
        person_posts=(_post(text="Edited"),),
        company_failure=failure,
    )

    # When
    result = await _run(
        _request(
            tmp_path,
            _settings(PERSON_URL, COMPANY_URL),
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
    initial = ApplicationClient(company_posts=(_post("company-1", actor_id="202"),))
    _ = await _run(_request(tmp_path, _settings(PERSON_URL, COMPANY_URL)), (initial,))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    prior = _tree(tmp_path / "previous")
    partial = ApplicationClient(
        company_failure=BrightDataError(BrightDataErrorCategory.NOT_FOUND)
    )

    # When
    result = await _run(
        _request(
            tmp_path,
            _settings(PERSON_URL, COMPANY_URL),
            paths=("previous", "partial"),
        ),
        (partial,),
    )

    # Then
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.candidate_change is CandidateChange.UNCHANGED
    assert _tree(tmp_path / "partial") == prior


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
    result = await _run(
        _request(tmp_path, _settings(PERSON_URL, keys="synthetic-one\nsynthetic-two")),
        clients,
    )

    # Then
    assert result.exit_code is CollectionExitCode.PROVIDER
    assert result.candidate_change is CandidateChange.ABSENT
    assert not (tmp_path / "candidate").exists()


@pytest.mark.anyio
async def test_schema_abort_writes_no_candidate(tmp_path: Path) -> None:
    # Given
    client = ApplicationClient(person_identity=None)

    # When
    result = await _run(_request(tmp_path, _settings(PERSON_URL)), (client,))

    # Then
    assert result.exit_code is CollectionExitCode.INTEGRITY
    assert not (tmp_path / "candidate").exists()


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
    result = await _run(
        _request(tmp_path, _settings(shared_alias)),
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
    _ = await _run(_request(tmp_path, _settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    client = ApplicationClient(
        person_failure=BrightDataError(
            BrightDataErrorCategory.SNAPSHOT_TERMINAL,
            snapshot_accepted=True,
        )
    )

    # When
    result = await _run(
        _request(tmp_path, _settings(PERSON_URL), paths=("previous", "partial")),
        (client,),
    )

    # Then
    assert result.exit_code is CollectionExitCode.PARTIAL
    assert result.candidate_change is CandidateChange.UNCHANGED
    assert [call[0] for call in client.calls] == ["person_posts"]


@pytest.mark.anyio
async def test_corrupt_prior_and_invalid_override_are_preflight_failures(
    tmp_path: Path,
) -> None:
    # Given
    previous = tmp_path / "previous"
    previous.mkdir()
    _ = (previous / "snapshot.json").write_text("not-json")

    # When
    corrupt = await _run(
        _request(tmp_path, _settings(PERSON_URL)), (ApplicationClient(),)
    )
    invalid = await _run(
        _request(
            tmp_path,
            _settings(PERSON_URL),
            paths=("absent", "invalid"),
            window=(date(2026, 8, 1), None),
        ),
        (ApplicationClient(),),
    )

    # Then
    assert corrupt.exit_code is CollectionExitCode.INTEGRITY
    assert invalid.exit_code is CollectionExitCode.INPUT
    assert not (tmp_path / "candidate").exists()
    assert not (tmp_path / "invalid").exists()


@pytest.mark.anyio
async def test_explicit_window_replaces_defaults(tmp_path: Path) -> None:
    # Given
    client = ApplicationClient()

    # When
    result = await _run(
        _request(
            tmp_path,
            _settings(PERSON_URL),
            window=(date(2026, 7, 1), date(2026, 7, 2)),
        ),
        (client,),
    )

    # Then
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert client.calls[-1] == (
        "person_posts",
        ((date(2026, 7, 1), date(2026, 7, 2)),),
    )


@pytest.mark.anyio
async def test_write_fault_returns_integrity_and_preserves_prior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _ = await _run(_request(tmp_path, _settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    prior = _tree(tmp_path / "previous")

    def fail_write(self: SnapshotRepository, state: SnapshotState) -> None:
        _ = self, state
        reason = "synthetic write interruption"
        raise SnapshotIntegrityError(reason)

    monkeypatch.setattr(SnapshotRepository, "write", fail_write)

    # When
    result = await _run(
        _request(tmp_path, _settings(PERSON_URL), paths=("previous", "fault")),
        (ApplicationClient(person_posts=(_post(text="Edited"),)),),
    )

    # Then
    assert result.exit_code is CollectionExitCode.INTEGRITY
    assert _tree(tmp_path / "previous") == prior
    assert not (tmp_path / "fault").exists()
    assert "synthetic-key" not in repr(result)
    assert PERSON_URL not in repr(result)


def test_router_status_enum_remains_closed() -> None:
    # Given / When / Then
    assert tuple(RouterRunStatus) == (
        RouterRunStatus.SUCCESS,
        RouterRunStatus.PARTIAL,
        RouterRunStatus.ABORTED,
    )
