"""Apify REST client for the approved Xquik Actor."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final, Self, final

import anyio
import structlog
from pydantic import ValidationError

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.providers.apify.constants import (
    APIFY_X_ACTOR,
    APIFY_X_REPORT_KEY,
    RUN_TIMEOUT_SECONDS,
)
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.providers.apify.runner import ApifyActorRunner
from social_media_subscriber.providers.apify.x_models import (
    ApifyXDiagnostic,
    ApifyXPost,
    ApifyXRunReport,
)
from social_media_subscriber.providers.http import HttpClientConfig

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType

    import httpx2

    from social_media_subscriber.providers.apify.x_requests import ApifyXPostInput
    from social_media_subscriber.serialization.json import JsonValue

_DEFAULT_HTTP_CONFIG: Final = HttpClientConfig(base_url="https://api.apify.com")
_LOGGER = structlog.stdlib.get_logger()


class _Completion(StrEnum):
    COMPLETE = "complete"
    BEST_EFFORT = "best_effort"
    ZERO_OUTPUT = "zero_output"


@final
class ApifyXClient:
    """One credential-bound Xquik Actor client."""

    def __init__(
        self,
        api_key: str,
        config: HttpClientConfig = _DEFAULT_HTTP_CONFIG,
        *,
        sleeper: Callable[[float], Awaitable[None]] = anyio.sleep,
        run_timeout_seconds: float = RUN_TIMEOUT_SECONDS,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a credential-bound Xquik client."""
        self._runner = ApifyActorRunner(
            api_key,
            config,
            sleeper=sleeper,
            run_timeout_seconds=run_timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        """Open the owned connection pool."""
        _ = await self._runner.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the owned connection pool."""
        _ = exc_type, exc_value, traceback
        await self.aclose()

    async def aclose(self) -> None:
        """Close the owned connection pool."""
        await self._runner.aclose()

    async def collect_posts(self, request: ApifyXPostInput) -> tuple[ApifyXPost, ...]:
        """Run one lifecycle-aware scrape and return the inclusive local window."""
        try:
            actor_input = request.as_json()
        except (AccountInputError, OverflowError, ValueError):
            raise ApifyError(ApifyErrorCategory.INPUT) from None
        run = await self._runner.run(APIFY_X_ACTOR, actor_input)
        store_id = run.default_key_value_store_id
        dataset_id = run.default_dataset_id
        if store_id is None or dataset_id is None:
            raise ApifyError(ApifyErrorCategory.RUN_TERMINAL, run_accepted=True)
        report = await self._report(store_id)
        completion = _completion(report)
        if completion in {_Completion.COMPLETE, _Completion.BEST_EFFORT}:
            values = await self._runner.download_dataset(dataset_id)
            posts, diagnostics = _parse_dataset(values)
            _validate_counts(report, len(posts), len(diagnostics), len(values))
            if diagnostics:
                raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=True)
            empty_pagination_result = (
                report.results.completion_reason == "pagination_safety_limit"
                and report.results.failed_subtargets == 0
                and not any(report.anomaly_counts.values())
            )
            if not posts and not empty_pagination_result:
                raise ApifyError(ApifyErrorCategory.INCOMPLETE, run_accepted=True)
            if completion is _Completion.BEST_EFFORT:
                _LOGGER.warning(
                    "provider.x.partial_results",
                    posts=len(posts),
                    failed_subtargets=report.results.failed_subtargets,
                    anomaly_count=sum(report.anomaly_counts.values()),
                )
            return tuple(
                post
                for post in posts
                if request.start_date <= post.timestamp.date() <= request.end_date
            )
        values = await self._runner.download_dataset(dataset_id)
        posts, diagnostics = _parse_dataset(values)
        _validate_counts(report, len(posts), len(diagnostics), len(values))
        if posts or len(diagnostics) != 1:
            raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=True)
        return ()

    async def _report(self, store_id: str) -> ApifyXRunReport:
        content = await self._runner.download_record(store_id, APIFY_X_REPORT_KEY)
        try:
            return ApifyXRunReport.model_validate_json(content)
        except ValidationError:
            raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=True) from None


def _completion(report: ApifyXRunReport) -> _Completion:
    results = report.results
    if report.outcome in {"complete", "partial"}:
        if (
            results.completion_reason == "source_exhausted"
            and results.failed_subtargets == 0
            and not any(report.anomaly_counts.values())
        ):
            return _Completion.COMPLETE
        return _Completion.BEST_EFFORT
    if (
        report.outcome == "zero-output"
        and results.completion_reason == "zero_output"
        and results.failed_subtargets == 0
        and not any(report.anomaly_counts.values())
    ):
        return _Completion.ZERO_OUTPUT
    raise ApifyError(ApifyErrorCategory.INCOMPLETE, run_accepted=True)


def _parse_dataset(
    values: tuple[JsonValue, ...],
) -> tuple[tuple[ApifyXPost, ...], tuple[ApifyXDiagnostic, ...]]:
    posts: list[ApifyXPost] = []
    diagnostics: list[ApifyXDiagnostic] = []
    for value in values:
        parsed = _parse_dataset_value(value)
        if isinstance(parsed, ApifyXDiagnostic):
            diagnostics.append(parsed)
        else:
            posts.append(parsed)
    return tuple(posts), tuple(diagnostics)


def _parse_dataset_value(value: JsonValue) -> ApifyXPost | ApifyXDiagnostic:
    if not isinstance(value, dict):
        raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=True)
    result_type = value.get("resultType")
    try:
        if result_type == "diagnostic":
            return ApifyXDiagnostic.model_validate(value)
        if result_type is None:
            return ApifyXPost.model_validate(value)
    except ValidationError:
        raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=True) from None
    raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=True)


def _validate_counts(
    report: ApifyXRunReport,
    post_count: int,
    diagnostic_count: int,
    total_count: int,
) -> None:
    results = report.results
    if (
        results.real_rows != post_count
        or results.diagnostic_rows != diagnostic_count
        or results.total_pushed != total_count
    ):
        raise ApifyError(ApifyErrorCategory.SCHEMA, run_accepted=True)
