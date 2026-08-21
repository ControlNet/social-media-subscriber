"""Atomic Bright Data Posts discovery for unknown LinkedIn locators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.identity import AccountIdentityService
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters.instance import (
    CollectedAccount,
    ResolvedLocatorPosts,
    UnresolvedLocatorPosts,
)
from social_media_subscriber.domain.platform import AccountKind
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataLocatorPostBatchResult,
)
from social_media_subscriber.providers.brightdata.discovery import (
    ResolvedPostsAccountDiscovery,
    UnresolvedPostsAccountDiscovery,
    derive_account_from_posts,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput

if TYPE_CHECKING:
    from datetime import datetime

    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.instance import AdapterPostLocatorRequest
    from social_media_subscriber.providers.brightdata.adapter_contracts import (
        BrightDataClientContract,
    )
    from social_media_subscriber.providers.brightdata.models import BrightDataPost


def _ownership_error() -> BrightDataNormalizationError:
    return BrightDataNormalizationError(BrightDataNormalizationErrorCategory.OWNERSHIP)


@dataclass(frozen=True, slots=True)
class BrightDataLocatorPostCollector:
    """Collect, classify, and normalize complete locator batches atomically."""

    client: BrightDataClientContract
    first_seen_at: datetime

    async def collect(
        self,
        requests: tuple[AdapterPostLocatorRequest, ...],
    ) -> BrightDataLocatorPostBatchResult:
        """Return exactly one resolved or unresolved outcome per input locator."""
        records_by_locator: dict[str, list[BrightDataPost]] = {
            request.locator.canonical_url: [] for request in requests
        }
        for kind in (AccountKind.PERSON, AccountKind.COMPANY):
            selected = tuple(
                request for request in requests if request.locator.kind is kind
            )
            if not selected:
                continue
            inputs = tuple(
                PostDiscoveryInput(
                    request.locator.canonical_url,
                    request.start_date,
                    request.end_date,
                )
                for request in selected
            )
            records = await self._collect_kind(kind, inputs)
            for record in records:
                owner = self._record_locator(selected, record)
                records_by_locator[owner.canonical_url].append(record)

        outcomes: list[ResolvedLocatorPosts | UnresolvedLocatorPosts] = []
        resolved_ids: set[str] = set()
        for request in requests:
            derived = derive_account_from_posts(
                request.locator,
                tuple(records_by_locator[request.locator.canonical_url]),
                AccountIdentityService(()),
                self.first_seen_at,
            )
            match derived:
                case ResolvedPostsAccountDiscovery(
                    account=account,
                    normalization=normalization,
                ):
                    if account.id in resolved_ids:
                        raise _ownership_error()
                    resolved_ids.add(account.id)
                    outcomes.append(
                        ResolvedLocatorPosts(
                            request.locator,
                            account,
                            CollectedAccount(
                                account.id,
                                normalization.posts,
                                normalization.source_records,
                                normalization.skipped,
                            ),
                        )
                    )
                case UnresolvedPostsAccountDiscovery():
                    outcomes.append(UnresolvedLocatorPosts(request.locator))
        return BrightDataLocatorPostBatchResult(tuple(outcomes))

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
    def _record_locator(
        requests: tuple[AdapterPostLocatorRequest, ...],
        record: BrightDataPost,
    ) -> LinkedInLocator:
        requested = {
            request.locator.canonical_url: request.locator for request in requests
        }
        candidates: dict[str, LinkedInLocator] = {}
        for actor_url in (
            record.use_url,
            record.user_url,
            record.profile_url,
            record.company_url,
        ):
            if actor_url is None:
                continue
            try:
                actor = parse_linkedin_locator(actor_url)
            except AccountInputError:
                raise _ownership_error() from None
            owner = requested.get(actor.canonical_url)
            if owner is None or actor.kind is not owner.kind:
                raise _ownership_error()
            candidates[owner.canonical_url] = owner
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        if not candidates and len(requests) == 1:
            return requests[0].locator
        raise _ownership_error()
