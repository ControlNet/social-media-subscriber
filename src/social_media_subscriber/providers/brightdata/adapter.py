"""Decorated LinkedIn Adapter over the typed Bright Data client boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, final

from social_media_subscriber.adapters import (
    AdapterMetadata,
    AdapterOperation,
    adapter,
)
from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    AccountRejectionCategory,
    AdapterInstanceOrdinal,
    BatchCompleted,
    CollectedAccount,
    InvalidCredentialBatchFailure,
    QuotaBatchFailure,
    RejectedAccount,
    RetryableBatchFailure,
    SchemaBatchFailure,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    AccountPostRequest,
    BrightDataAdapterConfig,
    BrightDataClientContract,
    BrightDataPostBatchResult,
    FixedCollectionWindow,
)
from social_media_subscriber.providers.brightdata.adapter_identity import (
    BrightDataIdentityResolver,
)
from social_media_subscriber.providers.brightdata.adapter_posts import (
    BrightDataPostCollector,
)
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from pydantic import SecretStr

    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.instance import (
        AdapterAttempt,
        AdapterBatch,
        AdapterInstance,
    )
    from social_media_subscriber.adapters.protocol import AdapterDriver
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.providers.brightdata.normalization_outcomes import (
        AccountIdentityOutcome,
    )


class _DeclaredAdapter:
    adapter_metadata: ClassVar[AdapterMetadata]


@final
@adapter(
    platform=Platform.LINKEDIN,
    operations=(
        AdapterOperation.RESOLVE_ACCOUNT_IDENTITY,
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
    ),
    account_kinds=(AccountKind.PERSON, AccountKind.COMPANY),
    supports_batch=True,
)
class BrightDataLinkedInAdapter(_DeclaredAdapter):
    """One credential-bound automatic LinkedIn Adapter instance."""

    def __init__(
        self,
        client: BrightDataClientContract,
        ordinal: AdapterInstanceOrdinal,
        collection_window: FixedCollectionWindow,
        first_seen_at: datetime,
    ) -> None:
        """Bind one client and deterministic run context to an opaque ordinal."""
        self._client = client
        self.ordinal = ordinal
        self.driver_class: type[AdapterDriver] = BrightDataLinkedInAdapter
        self._window = collection_window
        self._first_seen_at = first_seen_at

    async def resolve_account_identity(
        self,
        locators: tuple[LinkedInLocator, ...],
        known_accounts: tuple[Account, ...],
    ) -> tuple[AccountIdentityOutcome, ...]:
        """Resolve unknown locators and atomically reconcile stable identities."""
        return await BrightDataIdentityResolver(
            self._client,
            self._first_seen_at,
        ).resolve(locators, known_accounts)

    async def collect_account_posts(
        self,
        requests: tuple[AccountPostRequest, ...],
    ) -> BrightDataPostBatchResult:
        """Collect and normalize complete records for kind-separated Accounts."""
        return await BrightDataPostCollector(
            self._client,
            self._first_seen_at,
        ).collect(requests)

    async def collect(self, batch: AdapterBatch) -> AdapterAttempt:
        """Map provider and normalization failures into Router classifications."""
        try:
            result = await self.collect_account_posts(
                tuple(
                    AccountPostRequest(
                        account,
                        self._window.start_date,
                        self._window.end_date,
                    )
                    for account in batch.accounts
                )
            )
        except BrightDataNormalizationError:
            return SchemaBatchFailure()
        except BrightDataError as error:
            return _map_provider_error(batch, error)
        return BatchCompleted(
            tuple(
                CollectedAccount(item.account_id, item.posts)
                for item in result.accounts
            )
        )


@final
@dataclass(frozen=True, slots=True)
class BrightDataAdapterFactory:
    """Create one Bright Data Adapter for each Router-approved credential."""

    config: BrightDataAdapterConfig
    client_builder: Callable[[str], BrightDataClientContract]

    def create(
        self,
        credential: SecretStr,
        ordinal: AdapterInstanceOrdinal,
    ) -> AdapterInstance:
        """Bind an approved credential to one opaque instance ordinal."""
        return BrightDataLinkedInAdapter(
            self.client_builder(credential.get_secret_value()),
            ordinal,
            self.config.collection_window,
            self.config.first_seen_at,
        )


def _map_provider_error(batch: AdapterBatch, error: BrightDataError) -> AdapterAttempt:
    if error.snapshot_accepted:
        return AcceptedSnapshotBatchFailure()
    match error.category:
        case BrightDataErrorCategory.AUTH:
            result: AdapterAttempt = InvalidCredentialBatchFailure()
        case BrightDataErrorCategory.QUOTA:
            result = QuotaBatchFailure()
        case BrightDataErrorCategory.NOT_FOUND:
            result = BatchCompleted(
                tuple(
                    RejectedAccount(account.id, AccountRejectionCategory.NOT_FOUND)
                    for account in batch.accounts
                )
            )
        case BrightDataErrorCategory.INPUT:
            result = BatchCompleted(
                tuple(
                    RejectedAccount(account.id, AccountRejectionCategory.INVALID)
                    for account in batch.accounts
                )
            )
        case BrightDataErrorCategory.RETRYABLE | BrightDataErrorCategory.TIMEOUT:
            result = RetryableBatchFailure()
        case (
            BrightDataErrorCategory.SNAPSHOT_TIMEOUT
            | BrightDataErrorCategory.SNAPSHOT_TERMINAL
            | BrightDataErrorCategory.SCHEMA
        ):
            result = SchemaBatchFailure()
    return result
