"""Complete Bright Data post collection and per-Account normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.domain.platform import AccountKind
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
    from social_media_subscriber.domain.account import Account
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
                owner = self._record_owner(
                    tuple(item.account for item in selected), record
                )
                records_by_account[owner].append(record)
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

    @staticmethod
    def _record_owner(
        accounts: tuple[Account, ...], record: BrightDataPost
    ) -> AccountId:
        candidates = accounts
        if record.user_id is not None:
            candidates = tuple(
                account
                for account in candidates
                if account.platform_account_id == record.user_id
            )
        for actor_url in (
            record.use_url,
            record.user_url,
            record.profile_url,
            record.company_url,
        ):
            if actor_url is None:
                continue
            try:
                locator = parse_linkedin_locator(actor_url)
            except AccountInputError:
                raise BrightDataNormalizationError(
                    BrightDataNormalizationErrorCategory.OWNERSHIP
                ) from None
            candidates = tuple(
                account
                for account in candidates
                if locator.kind is account.kind
                and locator.canonical_url in (account.profile_url, *account.url_aliases)
            )
        if len(candidates) != 1:
            raise BrightDataNormalizationError(
                BrightDataNormalizationErrorCategory.OWNERSHIP
            )
        return candidates[0].id
