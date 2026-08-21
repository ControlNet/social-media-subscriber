"""Decorated LinkedIn Adapter over the typed Bright Data client boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, final

from social_media_subscriber.accounts.identity import AccountIdentityConflictError
from social_media_subscriber.adapters import (
    AdapterMetadata,
    AdapterOperation,
    adapter,
)
from social_media_subscriber.adapters.instance import (
    AdapterInstanceOrdinal,
    AdapterPostLocatorAttempt,
    AdapterPostLocatorBatch,
    AdapterPostRequest,
    BatchCompleted,
    CollectedAccount,
    IdentityBatchCompleted,
    LocatorPostsBatchCompleted,
    SchemaBatchFailure,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.providers.brightdata.adapter_discovery import (
    BrightDataLocatorPostCollector,
)
from social_media_subscriber.providers.brightdata.adapter_error_mapping import (
    map_identity_error,
    map_locator_error,
    map_provider_error,
)
from social_media_subscriber.providers.brightdata.adapter_identity import (
    BrightDataIdentityResolver,
)
from social_media_subscriber.providers.brightdata.adapter_posts import (
    BrightDataPostCollector,
)
from social_media_subscriber.providers.brightdata.errors import BrightDataError
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import SecretStr

    from social_media_subscriber.accounts.locator import LinkedInLocator
    from social_media_subscriber.adapters.instance import (
        AdapterAttempt,
        AdapterBatch,
        AdapterIdentityAttempt,
        AdapterIdentityBatch,
        AdapterInstance,
    )
    from social_media_subscriber.adapters.protocol import AdapterDriver
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.providers.brightdata.adapter_contracts import (
        BrightDataAdapterConfig,
        BrightDataClientContract,
        BrightDataPostBatchResult,
    )
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
        AdapterOperation.DISCOVER_LOCATOR_POSTS,
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
        config: BrightDataAdapterConfig,
    ) -> None:
        """Bind one client and deterministic run context to an opaque ordinal."""
        self._client = client
        self.ordinal = ordinal
        self.driver_class: type[AdapterDriver] = BrightDataLinkedInAdapter
        self._config = config

    async def aclose(self) -> None:
        """Close the credential-bound provider client."""
        await self._client.aclose()

    async def resolve_account_identity(
        self,
        locators: tuple[LinkedInLocator, ...],
        known_accounts: tuple[Account, ...],
    ) -> tuple[AccountIdentityOutcome, ...]:
        """Resolve unknown locators and atomically reconcile stable identities."""
        return await BrightDataIdentityResolver(
            self._client,
            self._config.first_seen_at,
        ).resolve(locators, known_accounts)

    async def collect_account_posts(
        self,
        requests: tuple[AdapterPostRequest, ...],
    ) -> BrightDataPostBatchResult:
        """Collect and normalize complete records for kind-separated Accounts."""
        return await BrightDataPostCollector(
            self._client,
            self._config.first_seen_at,
        ).collect(requests)

    async def collect(self, batch: AdapterBatch) -> AdapterAttempt:
        """Map provider and normalization failures into Router classifications."""
        try:
            result = await self.collect_account_posts(batch.requests)
        except BrightDataNormalizationError:
            return SchemaBatchFailure()
        except BrightDataError as error:
            return map_provider_error(batch, error)
        return BatchCompleted(
            tuple(
                CollectedAccount(
                    item.account_id,
                    item.posts,
                    item.source_records,
                    item.skipped,
                )
                for item in result.accounts
            )
        )

    async def discover_posts(
        self,
        batch: AdapterPostLocatorBatch,
    ) -> AdapterPostLocatorAttempt:
        """Collect locator Posts and map failures into Router classifications."""
        try:
            result = await BrightDataLocatorPostCollector(
                self._client,
                self._config.first_seen_at,
            ).collect(batch.requests)
        except BrightDataNormalizationError:
            return SchemaBatchFailure()
        except BrightDataError as error:
            return map_locator_error(batch, error)
        return LocatorPostsBatchCompleted(result.outcomes)

    async def resolve_identity(
        self,
        batch: AdapterIdentityBatch,
    ) -> AdapterIdentityAttempt:
        """Map provider identity outcomes into Router classifications."""
        try:
            outcomes = await self.resolve_account_identity(
                batch.locators,
                batch.known_accounts,
            )
        except (AccountIdentityConflictError, BrightDataNormalizationError):
            return SchemaBatchFailure()
        except BrightDataError as error:
            return map_identity_error(len(batch.locators), error)
        return IdentityBatchCompleted(outcomes)


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
            self.config,
        )
