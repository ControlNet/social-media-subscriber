"""Complete Bright Data post collection and per-Account normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from social_media_subscriber.domain.platform import AccountKind
from social_media_subscriber.providers.brightdata.actor_ownership import (
    actor_account_id,
)
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataPostBatchResult,
    CollectedAccountPosts,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalize import normalize_posts
from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput

if TYPE_CHECKING:
    from datetime import datetime

    from social_media_subscriber.adapters.instance import AdapterPostRequest
    from social_media_subscriber.domain.ids import AccountId
    from social_media_subscriber.providers.brightdata.adapter_contracts import (
        BrightDataClientContract,
    )
    from social_media_subscriber.providers.brightdata.models import BrightDataPost


@dataclass(frozen=True, slots=True)
class BrightDataPostCollector:
    """Collect kind-separated records and return deterministic Account results."""

    client: BrightDataClientContract
    first_seen_at: datetime

    async def collect(
        self,
        requests: tuple[AdapterPostRequest, ...],
    ) -> BrightDataPostBatchResult:
        """Normalize all records atomically after ownership classification."""
        if len({request.account.id for request in requests}) != len(requests):
            raise BrightDataNormalizationError(
                BrightDataNormalizationErrorCategory.DUPLICATE
            )
        records_by_account: dict[AccountId, list[BrightDataPost]] = {
            request.account.id: [] for request in requests
        }
        for kind in (AccountKind.PERSON, AccountKind.COMPANY):
            selected = tuple(
                request for request in requests if request.account.kind is kind
            )
            if not selected:
                continue
            inputs = tuple(
                PostDiscoveryInput(
                    request.account.profile_url,
                    request.start_date,
                    request.end_date,
                )
                for request in selected
            )
            records = await self._collect_kind(kind, inputs)
            for record in records:
                owner = actor_account_id(record, kind)
                try:
                    records_by_account[owner].append(record)
                except KeyError:
                    raise BrightDataNormalizationError(
                        BrightDataNormalizationErrorCategory.OWNERSHIP
                    ) from None
        outcomes: list[CollectedAccountPosts] = []
        for request in requests:
            normalized = normalize_posts(
                request.account,
                tuple(records_by_account[request.account.id]),
                self.first_seen_at,
            )
            outcomes.append(
                CollectedAccountPosts(
                    request.account.id,
                    normalized.source_records,
                    normalized.posts,
                    normalized.skipped,
                )
            )
        return BrightDataPostBatchResult(tuple(outcomes))

    async def _collect_kind(
        self,
        kind: AccountKind,
        inputs: tuple[PostDiscoveryInput, ...],
    ) -> tuple[BrightDataPost, ...]:
        match kind:
            case AccountKind.PERSON:
                return await self.client.collect_person_posts(inputs)
            case AccountKind.COMPANY:
                return await self.client.collect_company_posts(inputs)
        assert_never(kind)
