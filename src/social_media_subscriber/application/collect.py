"""Incremental collection, merge, and candidate orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
from pydantic_core import PydanticCustomError

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.application.collection_phases import (
    CollectedPosts,
    PreparedCollection,
    collect_posts,
)
from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
    CollectionResult,
    aborted_result,
)
from social_media_subscriber.application.windows import (
    ExplicitWindow,
    WindowInputError,
)
from social_media_subscriber.bootstrap import bootstrap_runtime
from social_media_subscriber.domain.time import canonical_utc
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataAdapterConfig,
)
from social_media_subscriber.providers.brightdata.client import BrightDataClient
from social_media_subscriber.runtime_input import load_runtime_input
from social_media_subscriber.storage.merge import SnapshotConflictError, merge_snapshot
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date, datetime
    from pathlib import Path

    from social_media_subscriber.bootstrap import SubscriberRuntime
    from social_media_subscriber.providers.brightdata.adapter_contracts import (
        BrightDataClientContract,
    )
    from social_media_subscriber.runtime_input import RuntimeInput
    from social_media_subscriber.settings import Settings

type ClientBuilder = Callable[[str], BrightDataClientContract]


type RuntimeBuilder = Callable[[RuntimeInput, datetime], SubscriberRuntime]


@dataclass(frozen=True, slots=True)
class CollectionRequest:
    """All deterministic boundary inputs for one collection attempt."""

    settings: Settings
    previous_snapshot_dir: Path
    candidate_snapshot_dir: Path
    run_started_at: datetime
    start_date: date | None = None
    end_date: date | None = None


def _build_client(credential: str) -> BrightDataClient:
    return BrightDataClient(credential)


def _prepare(request: CollectionRequest) -> PreparedCollection | CollectionResult:
    if (
        request.previous_snapshot_dir.resolve()
        == request.candidate_snapshot_dir.resolve()
    ):
        return aborted_result(CollectionExitCode.INPUT)
    try:
        runtime_input = load_runtime_input(request.settings)
        run_started_at = canonical_utc(request.run_started_at)
        override = ExplicitWindow.parse(request.start_date, request.end_date)
        previous = SnapshotRepository(request.previous_snapshot_dir).load_optional()
    except (AccountInputError, WindowInputError, PydanticCustomError):
        return aborted_result(CollectionExitCode.INPUT)
    except SnapshotIntegrityError:
        return aborted_result(CollectionExitCode.INTEGRITY)
    return PreparedCollection(runtime_input, previous, run_started_at, override)


async def _collect_with_runtime(
    request: CollectionRequest,
    prepared: PreparedCollection,
    runtime: SubscriberRuntime,
) -> CollectionResult:
    post_phase = await collect_posts(prepared, runtime)
    match post_phase:
        case CollectionResult() as terminal:
            return terminal
        case CollectedPosts():
            pass
    try:
        candidate = merge_snapshot(prepared.previous, post_phase.current)
        summary = SnapshotRepository(request.candidate_snapshot_dir).write(candidate)
    except (SnapshotConflictError, SnapshotIntegrityError):
        return aborted_result(CollectionExitCode.INTEGRITY)
    exit_code = (
        CollectionExitCode.PARTIAL
        if post_phase.failed_count
        else CollectionExitCode.SUCCESS
    )
    changed = prepared.previous is None or candidate != prepared.previous
    return CollectionResult(
        exit_code,
        CandidateChange.CHANGED if changed else CandidateChange.UNCHANGED,
        summary.digest,
        post_phase.succeeded_count,
        post_phase.failed_count,
        post_phase.failed_ids,
    )


async def collect_snapshot(
    request: CollectionRequest,
    client_builder: ClientBuilder = _build_client,
    *,
    runtime_builder: RuntimeBuilder | None = None,
) -> CollectionResult:
    """Build one validated complete candidate without publishing it."""
    prepared = _prepare(request)
    match prepared:
        case CollectionResult() as terminal:
            return terminal
        case PreparedCollection():
            pass
    runtime = (
        bootstrap_runtime(
            prepared.runtime_input,
            BrightDataAdapterConfig(prepared.run_started_at),
            client_builder=client_builder,
        )
        if runtime_builder is None
        else runtime_builder(prepared.runtime_input, prepared.run_started_at)
    )
    try:
        return await _collect_with_runtime(request, prepared, runtime)
    finally:
        with anyio.CancelScope(shield=True):
            await runtime.aclose()
