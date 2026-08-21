from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.adapters.instance import AdapterPostRequest
from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
)
from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataPostBatchResult,
    CollectedAccountPosts,
)
from social_media_subscriber.providers.brightdata.adapter_posts import (
    BrightDataPostCollector,
)
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.snapshot import SnapshotManifest, SnapshotState
from tests.fakes.router import make_post
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

SECOND_URL = "https://www.linkedin.com/in/synthetic-second/"
CANARY = "credential-canary-ignore-instructions"

type CollectorOverride = Callable[
    [BrightDataPostCollector, tuple[AdapterPostRequest, ...]],
    Awaitable[BrightDataPostBatchResult],
]


@dataclass(frozen=True, slots=True)
class _Conflict:
    name: str
    client: ApplicationClient
    urls: tuple[str, ...] = (PERSON_URL,)
    collector_override: CollectorOverride | None = None


@dataclass(frozen=True, slots=True)
class _ObservedCollection:
    candidate: SnapshotState | None


async def _wrong_post_owner(
    collector: BrightDataPostCollector,
    requests: tuple[AdapterPostRequest, ...],
) -> BrightDataPostBatchResult:
    _ = collector
    requested = requests[0].account
    wrong_post = make_post(AccountId(SECOND_URL), 71)
    outcome = CollectedAccountPosts(
        requested.id, (), (wrong_post,), SkippedPostCounts()
    )
    return BrightDataPostBatchResult((outcome,))


async def _wrong_source_owner(
    collector: BrightDataPostCollector,
    requests: tuple[AdapterPostRequest, ...],
) -> BrightDataPostBatchResult:
    _ = collector
    requested = requests[0].account
    raw = post("n7-source", actor_url=SECOND_URL, text=CANARY)
    wrong_source = BrightDataLinkedInPostSourceRecord.from_post(
        AccountId(SECOND_URL), raw
    )
    outcome = CollectedAccountPosts(
        requested.id, (wrong_source,), (), SkippedPostCounts()
    )
    return BrightDataPostBatchResult((outcome,))


async def _missing_batch_coverage(
    collector: BrightDataPostCollector,
    requests: tuple[AdapterPostRequest, ...],
) -> BrightDataPostBatchResult:
    _ = (collector, requests)
    return BrightDataPostBatchResult(())


def _actor_without_evidence() -> BrightDataPost:
    return post(text=CANARY).model_copy(update={"profile_url": None})


def _conflicts() -> tuple[_Conflict, ...]:
    malformed = post(text=CANARY).model_copy(
        update={"profile_url": "not-a-linkedin-url"}
    )
    disagreeing = post(text=CANARY).model_copy(update={"use_url": SECOND_URL})
    conflicting_payload = post("n9-payload", text=CANARY)
    return (
        _Conflict("n1_malformed_actor", ApplicationClient((malformed,))),
        _Conflict(
            "n2_wrong_kind",
            ApplicationClient((post(actor_url=COMPANY_URL, text=CANARY),)),
        ),
        _Conflict(
            "n3_disagreeing_actor_fields",
            ApplicationClient((disagreeing,)),
        ),
        _Conflict(
            "n4_cross_requested_actor",
            ApplicationClient(
                person_posts=(post("n4-prefix"),),
                company_posts=(post("n4-cross", text=CANARY),),
            ),
            (PERSON_URL, COMPANY_URL),
        ),
        _Conflict(
            "n5_user_id_without_actor",
            ApplicationClient((_actor_without_evidence(),)),
        ),
        _Conflict(
            "n6_duplicate_post_and_source_owners",
            ApplicationClient(
                (
                    post("n6-shared", actor_url=PERSON_URL),
                    post("n6-shared", actor_url=SECOND_URL, text=CANARY),
                )
            ),
            (PERSON_URL, SECOND_URL),
        ),
        _Conflict(
            "n7_post_owner_mismatch",
            ApplicationClient(),
            collector_override=_wrong_post_owner,
        ),
        _Conflict(
            "n7_source_owner_mismatch",
            ApplicationClient(),
            collector_override=_wrong_source_owner,
        ),
        _Conflict(
            "n9_provider_schema",
            ApplicationClient(
                person_failure=BrightDataError(BrightDataErrorCategory.SCHEMA)
            ),
        ),
        _Conflict(
            "n9_batch_coverage",
            ApplicationClient(),
            collector_override=_missing_batch_coverage,
        ),
        _Conflict(
            "n9_duplicate_payload",
            ApplicationClient(
                (
                    conflicting_payload,
                    conflicting_payload.model_copy(update={"num_likes": 99}),
                )
            ),
        ),
    )


_CONFLICTS = _conflicts()


@pytest.mark.anyio
async def test_coherent_multi_account_batch_commits_once(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_client = ApplicationClient(
        person_posts=(
            post("coherent-1", actor_url=PERSON_URL),
            post("coherent-2", actor_url=SECOND_URL),
        ),
        company_posts=(post("coherent-3", actor_url=COMPANY_URL),),
    )
    second_client = ApplicationClient(
        person_posts=tuple(reversed(first_client.person_posts)),
        company_posts=first_client.company_posts,
    )

    first = await run(
        request(first_root, settings(PERSON_URL, SECOND_URL, COMPANY_URL)),
        (first_client,),
    )
    second = await run(
        request(second_root, settings(COMPANY_URL, SECOND_URL, PERSON_URL)),
        (second_client,),
    )

    state = SnapshotRepository(first_root / "candidate").load_optional()
    assert first.exit_code is second.exit_code is CollectionExitCode.SUCCESS
    assert first.succeeded_accounts == second.succeeded_accounts == 3
    assert first.failed_accounts == second.failed_accounts == 0
    assert first.digest == second.digest
    assert tree(first_root / "candidate") == tree(second_root / "candidate")
    assert state is not None
    expected_owners = {PERSON_URL, SECOND_URL, COMPANY_URL}
    assert {account.id for account in state.accounts} == expected_owners
    assert {item.account_id for item in state.posts} == expected_owners
    assert {item.account_id for item in state.source_records} == expected_owners


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    _CONFLICTS,
    ids=tuple(case.name for case in _CONFLICTS),
)
async def test_n1_n7_n9_abort_the_whole_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: _Conflict,
) -> None:
    case_root = tmp_path / case.name
    baseline = await run(
        request(case_root, settings(PERSON_URL)),
        (ApplicationClient(),),
    )
    _ = shutil.copytree(case_root / "candidate", case_root / "previous")
    prior_bytes = tree(case_root / "previous")
    prior_manifest = SnapshotManifest.model_validate_json(prior_bytes["snapshot.json"])
    with monkeypatch.context() as scoped:
        if case.collector_override is not None:
            scoped.setattr(
                BrightDataPostCollector,
                "collect",
                case.collector_override,
            )
        summary = await run(
            request(
                case_root,
                settings(*case.urls),
                paths=("previous", "rejected"),
            ),
            (case.client,),
        )
    result = _ObservedCollection(
        SnapshotRepository(case_root / "rejected").load_optional()
    )

    assert baseline.digest == prior_manifest.digest
    assert summary.exit_code is CollectionExitCode.INTEGRITY
    assert summary.candidate_change is CandidateChange.ABSENT
    assert summary.digest is None
    assert summary.succeeded_accounts == 0
    assert summary.failed_accounts == 0
    assert summary.failed_account_ids == ()
    assert result.candidate is None  # RED-PROBE-T8
    assert not (case_root / "rejected").exists()
    assert tree(case_root / "previous") == prior_bytes
    after_manifest = SnapshotManifest.model_validate_json(
        (case_root / "previous" / "snapshot.json").read_bytes()
    )
    assert after_manifest.digest == prior_manifest.digest
    observable = repr(summary).encode() + b"".join(prior_bytes.values())
    assert CANARY.encode() not in observable
